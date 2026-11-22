"""Backend-for-frontend: session, A2UI streaming, binding fill and approvals.

- /api/chat streams one agent turn as Server-Sent Events. A2UI messages
  (createSurface, updateComponents, updateDataModel) go out on `a2ui` events.
- The agent sends layout with @path bindings. The BFF fills each bound path by
  calling the gateway under the user's identity, so on-screen values come from
  the core, pass the same policy checks, and never come from the model.
- Approve / reject clicks never reach the agent: the BFF mints a one-time
  approval token for the clicking user and calls the money-moving tool itself.
- Every surface shown and every click is written to the decision record.
"""
import hashlib
import json
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from poc.agent.scripted import LENDING_CATALOG_ID, ScriptedAgent
from poc.common.config import GATEWAY_URL, MCP_PROTOCOL_VERSION, SERVICE_TOKEN, STUBS_URL
from poc.common.tokens import PERSONAS, issue_user_token, mint_approval_token, verify_user_token

app = FastAPI(title="Lending BFF (POC)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
BFF_AGENT_ID = "lending-bff"


class ChatIn(BaseModel):
    persona: str
    message: str


class ActionIn(BaseModel):
    persona: str
    action: dict


def _persona(p: str) -> str:
    if p not in PERSONAS:
        raise HTTPException(400, f"unknown persona {p}")
    return p


def _gateway_call(token: str, name: str, args: dict, trace_id: str, case_id: str | None = None) -> dict:
    r = httpx.post(f"{GATEWAY_URL}/mcp", timeout=30, headers={
        "Authorization": f"Bearer {token}", "MCP-Protocol-Version": MCP_PROTOCOL_VERSION, "Mcp-Method": "tools/call",
        "Mcp-Name": name, "X-Trace-Id": trace_id, **({"X-Case-Id": case_id} if case_id else {})},
        json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "tools/call",
              "params": {"name": name, "arguments": args}})
    r.raise_for_status()
    return r.json()["result"]


def _record(kind: str, **fields) -> None:
    try:
        httpx.post(f"{GATEWAY_URL}/records", headers={"X-Service-Token": SERVICE_TOKEN},
                   json={"kind": kind, **fields}, timeout=10)
    except httpx.HTTPError:
        pass  # audit write failures would page on-call in production


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _surface_messages(ev: dict, bff_token: str, claims: dict, trace_id: str) -> list[dict]:
    sid = ev["surface_id"]
    msgs = [
        {"version": "1.0", "createSurface": {"surfaceId": sid, "catalogId": LENDING_CATALOG_ID}},
        {"version": "1.0", "updateComponents": {"surfaceId": sid, "components": ev["components"]}},
    ]
    filled = {}
    for path, src in ev.get("bindings", {}).items():
        res = _gateway_call(bff_token, src["tool"], src["args"], trace_id, src["args"].get("case_id"))
        value = res.get("structuredContent") or {}
        if res.get("isError"):
            value = {"_denied": value.get("reason_code", "error")}
        filled[path] = value
    filled.update(ev.get("data", {}))
    for path, value in filled.items():
        msgs.append({"version": "1.0", "updateDataModel": {"surfaceId": sid, "path": path, "value": value}})
    case_id = next((s["args"].get("case_id") for s in ev.get("bindings", {}).values()), None) or sid.split("-")[1]
    _record("surface_shown", case_id=case_id, trace_id=trace_id, actor=claims["sub"], role=claims["role"],
            agent=BFF_AGENT_ID, decision="SHOWN", detail={
                "surface_id": sid, "components": [c["component"] for c in ev["components"]],
                "bound_paths": {p: s["tool"] for p, s in ev.get("bindings", {}).items()},
                "data_sha256": hashlib.sha256(json.dumps(filled, sort_keys=True, default=str).encode()).hexdigest()[:16]})
    return msgs


@app.get("/api/personas")
def personas():
    return [{"id": k, **v} for k, v in PERSONAS.items()]


@app.post("/api/chat")
def chat(body: ChatIn):
    persona = _persona(body.persona)
    user_token = issue_user_token(persona)                      # agent acts for the user
    bff_token = issue_user_token(persona, agent_id=BFF_AGENT_ID)  # BFF fills bindings for the same user
    claims = verify_user_token(user_token)
    trace_id = uuid.uuid4().hex[:12]

    def stream():
        yield _sse("start", {"trace_id": trace_id, "persona": claims["name"]})
        try:
            for ev in ScriptedAgent(user_token, claims, trace_id).run(body.message):
                if ev["type"] == "surface":
                    yield _sse("a2ui", {"messages": _surface_messages(ev, bff_token, claims, trace_id)})
                else:
                    yield _sse(ev["type"], ev)
        except Exception as e:  # surface errors to the demo instead of hanging
            yield _sse("text", {"type": "text", "text": f"Error: {e}"})
        yield _sse("done", {"trace_id": trace_id})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/action")
def action(body: ActionIn):
    persona = _persona(body.persona)
    claims = {**PERSONAS[persona], "agent_id": BFF_AGENT_ID}
    a = body.action
    ctx = a.get("context") or {}
    trace_id = uuid.uuid4().hex[:12]
    case_id = str(ctx.get("case_id", ""))
    _record("user_action", case_id=case_id, trace_id=trace_id, actor=claims["sub"], role=claims["role"],
            agent=BFF_AGENT_ID, decision="CLICKED", detail={"action": a.get("name"), "context": ctx,
                                                            "surface_id": a.get("surfaceId")})
    if a.get("name") != "approve_sanction":
        return {"ok": False, "message": f"Unhandled action {a.get('name')}"}

    # Human gesture -> one-time approval token for this user, case and decision.
    approval = mint_approval_token(claims, case_id, ctx.get("decision", "APPROVE"))
    _record("approval_token_minted", case_id=case_id, trace_id=trace_id, actor=claims["sub"], role=claims["role"],
            agent="los-approval-service", decision=ctx.get("decision"), detail={"ttl_seconds": 120})
    res = _gateway_call(issue_user_token(persona, agent_id=BFF_AGENT_ID), "los.approve_sanction",
                        {"case_id": case_id, "decision": ctx.get("decision", "APPROVE"), "approval_token": approval},
                        trace_id, case_id)
    if res.get("isError"):
        d = res.get("structuredContent") or {}
        return {"ok": False, "reason_code": d.get("reason_code"), "message": d.get("message")}
    return {"ok": True, "message": f"Case {case_id}: {ctx.get('decision')} recorded by {claims['name']}."}


@app.post("/api/admin/tamper")
def tamper(body: dict):
    r = httpx.post(f"{STUBS_URL}/admin/tamper", json=body, timeout=10)
    return r.json()


@app.post("/api/admin/reset")
def reset():
    httpx.post(f"{STUBS_URL}/admin/reset", timeout=60).raise_for_status()
    r = httpx.post(f"{GATEWAY_URL}/admin/reset", headers={"X-Service-Token": SERVICE_TOKEN}, timeout=10)
    return r.json()


@app.get("/api/records")
def get_records(case_id: str | None = None):
    return httpx.get(f"{GATEWAY_URL}/records", params={"case_id": case_id} if case_id else {}, timeout=10).json()


@app.get("/api/registry")
def get_registry():
    return httpx.get(f"{GATEWAY_URL}/registry", timeout=20).json()


@app.get("/healthz")
def healthz():
    return {"ok": True}
