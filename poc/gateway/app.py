"""Mini MCP gateway: a local stand-in for AgentCore Gateway + Policy + Identity.

Every tool call, from the agent or from code mode, passes the same checks:

  1. Inbound auth     - verify the bearer JWT; the agent ID must be in the registry
  2. Request interceptor - registry check (tool registered, active, agent allowed),
                        live pin check, policy context (loan branch, approval token,
                        code-mode scope), trace and case IDs
  3. Cedar policy     - deny by default, forbid wins (policies/*.cedar)
  4. Response interceptor - result shaping (large results become a summary),
                        audit entry in the decision record

Also exposes `x_search_tools` (stand-in for x_amz_bedrock_agentcore_search):
role-filtered tool discovery so the agent never loads every schema.
"""
import hashlib
import json
import re
import uuid

import cedarpy
import httpx
import jwt
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from poc.common import records
from poc.common.config import MCP_PROTOCOL_VERSION, POLICY_DIR, SERVICE_TOKEN, STUBS_URL
from poc.common.tokens import verify_approval_token, verify_user_token
from poc.gateway import registry as reg

app = FastAPI(title="Yutrix mini MCP gateway (POC)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SEARCH_TOOL = {
    "name": "x_search_tools",
    "description": "Search the tools you are allowed to use with a natural-language query. Returns names and descriptions.",
    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
}
AGENT_RESULT_LIMIT = 4000          # characters of tool result an agent turn may receive
USED_APPROVALS: set[str] = set()   # single-use approval tokens (jti)
ALERTED_PINS: set[str] = set()


# ------------------------------------------------------------------ policy
def _load_policies() -> tuple[str, list[str]]:
    text = "\n".join(p.read_text() for p in sorted(POLICY_DIR.glob("*.cedar")))
    ids = re.findall(r'@id\("([^"]+)"\)', text)
    return text, ids


POLICIES, POLICY_IDS = _load_policies()


def _entities(claims: dict, registry: dict) -> list[dict]:
    ents = [{"uid": {"type": "User", "id": claims["sub"]},
             "attrs": {"role": claims["role"], "branch": claims["branch"],
                       "agent_registered": claims.get("agent_id") in registry["agents"]
                       and registry["agents"][claims["agent_id"]]["status"] == "active"},
             "parents": []}]
    for group in ("read", "write", "money-moving", "case-read"):
        ents.append({"uid": {"type": "Action", "id": group}, "attrs": {}, "parents": []})
    for name, t in registry["tools"].items():
        parents = [{"type": "Action", "id": "case-read"}]
        if t["tier"] == "read":
            parents.append({"type": "Action", "id": "read"})
        else:
            parents.append({"type": "Action", "id": "write"})
        if t["tier"] == "money-moving":
            parents.append({"type": "Action", "id": "money-moving"})
        ents.append({"uid": {"type": "Action", "id": name}, "attrs": {}, "parents": parents})
    return ents


def authorize(claims: dict, tool: str, context: dict, registry: dict) -> tuple[bool, list[str]]:
    req = {"principal": f'User::"{claims["sub"]}"', "action": f'Action::"{tool}"',
           "resource": 'Gateway::"lending-poc"', "context": context}
    r = cedarpy.is_authorized(req, POLICIES, _entities(claims, registry))
    reasons = []
    for pid in r.diagnostics.reasons:
        idx = int(pid.replace("policy", "")) if pid.startswith("policy") and pid[6:].isdigit() else None
        reasons.append(POLICY_IDS[idx] if idx is not None and idx < len(POLICY_IDS) else pid)
    if r.diagnostics.errors:
        reasons.append("policy-evaluation-error")
    return r.decision == cedarpy.Decision.Allow, reasons


# ------------------------------------------------------------------ helpers
def _claims(authorization: str | None) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return verify_user_token(authorization[7:])
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}") from e


async def _upstream(server: str, method: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{STUBS_URL}/mcp/{server}",
                              headers={"MCP-Protocol-Version": MCP_PROTOCOL_VERSION, "Mcp-Method": method},
                              json={"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params or {}})
        r.raise_for_status()
        return r.json()["result"]


async def _live_tools(registry: dict, trace_id: str, claims: dict | None = None) -> dict[str, dict]:
    """Upstream tool definitions that are registered, active and still match their pin."""
    out = {}
    for server in registry["servers"]:
        for t in (await _upstream(server, "tools/list"))["tools"]:
            fq = f"{server}.{t['name']}"
            entry = registry["tools"].get(fq)
            if not entry or entry["status"] != "active":
                continue  # unregistered tools are invisible
            if reg.tool_hash(t) != entry["pinned_sha256"]:
                if fq not in ALERTED_PINS:
                    ALERTED_PINS.add(fq)
                    records.append("pin_alert", trace_id=trace_id, tool=fq, decision="HIDDEN",
                                   reason="tool-definition-changed-since-approval",
                                   actor=claims and claims["sub"], role=claims and claims["role"],
                                   detail={"owner": entry["owner"], "live_description": t["description"],
                                           "pinned_sha256": entry["pinned_sha256"], "live_sha256": reg.tool_hash(t)})
                continue
            ALERTED_PINS.discard(fq)
            out[fq] = {**t, "name": fq}
    return out


async def _case_branch(case_id: str | None) -> str:
    if not case_id:
        return ""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{STUBS_URL}/internal/case-directory/{case_id}")
        return r.json().get("branch", "") if r.status_code == 200 else ""


def _visible_to(claims: dict, tools: dict, registry: dict) -> dict:
    """Role filter for listing and search: hide tools this caller could never use."""
    vis = {}
    for name, t in tools.items():
        if claims.get("agent_id") not in registry["tools"][name]["allowed_agents"]:
            continue
        ctx = {"loan_branch": claims["branch"], "approval_token_valid": True,
               "via_code": claims.get("scope") == "code", "case_in_scope": True}
        ok, _ = authorize(claims, name, ctx, registry)
        if ok:
            vis[name] = t
    return vis


def _search(query: str, tools: dict) -> list[dict]:
    words = [w for w in re.findall(r"[a-z]+", query.lower()) if len(w) > 2]
    scored = []
    for name, t in tools.items():
        hay = (name.replace(".", " ").replace("_", " ") + " " + t["description"]).lower()
        score = sum(hay.count(w) for w in words)
        if score:
            scored.append((score, {"name": name, "description": t["description"]}))
    return [s[1] for s in sorted(scored, key=lambda x: -x[0])[:5]]


def _shape(tool: str, data: dict, via: str) -> tuple[dict, bool]:
    """Response interceptor: keep agent context lean. Code mode gets full data."""
    if via == "code":
        return data, False
    if tool == "los.get_bank_transactions":
        txns = data.get("transactions", [])
        return {"case_id": data.get("case_id"), "count": len(txns),
                "from": txns[0]["txn_date"] if txns else None, "to": txns[-1]["txn_date"] if txns else None,
                "note": f"{len(txns)} rows withheld from the model's context. Analyse them with run_code."}, True
    text = json.dumps(data)
    if len(text) > AGENT_RESULT_LIMIT:
        return {"truncated": True, "preview": text[:AGENT_RESULT_LIMIT]}, True
    return data, False


def _rpc(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _denied(rid, code: str, message: str):
    return _rpc(rid, {"isError": True, "content": [{"type": "text", "text": f"DENIED [{code}]: {message}"}],
                      "structuredContent": {"denied": True, "reason_code": code, "message": message}})


# ------------------------------------------------------------------ MCP endpoint
@app.post("/mcp")
async def mcp(request: Request, authorization: str | None = Header(None),
              x_trace_id: str | None = Header(None), x_case_id: str | None = Header(None)):
    body = await request.json()
    rid, method, params = body.get("id"), body.get("method"), body.get("params") or {}
    trace_id = x_trace_id or uuid.uuid4().hex[:12]
    registry = reg.load()

    # 1. Inbound auth
    try:
        claims = _claims(authorization)
    except HTTPException as e:
        records.append("denial", trace_id=trace_id, case_id=x_case_id, decision="DENY", reason="inbound-auth",
                       detail={"error": e.detail})
        raise
    if claims.get("agent_id") not in registry["agents"]:
        records.append("denial", trace_id=trace_id, case_id=x_case_id, actor=claims["sub"], role=claims["role"],
                       agent=claims.get("agent_id"), decision="DENY", reason="agent-not-registered")
        raise HTTPException(403, "agent not registered")
    via = "code" if claims.get("scope") == "code" else ("bff" if claims.get("agent_id") == "lending-bff" else "agent")

    if method == "tools/list":
        tools = _visible_to(claims, await _live_tools(registry, trace_id, claims), registry)
        if claims.get("scope") == "code":
            tools = {k: v for k, v in tools.items() if k in claims.get("tools", [])}
        return _rpc(rid, {"tools": [SEARCH_TOOL, *tools.values()]})

    if method != "tools/call":
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unsupported method {method}"}}

    name, args = params.get("name", ""), dict(params.get("arguments") or {})
    case_id = str(args.get("case_id") or x_case_id or "") or None
    base = dict(trace_id=trace_id, case_id=case_id, actor=claims["sub"], role=claims["role"],
                agent=claims.get("agent_id"), tool=name, via=via)

    if name == "x_search_tools":
        tools = _visible_to(claims, await _live_tools(registry, trace_id, claims), registry)
        hits = _search(args.get("query", ""), tools)
        records.append("tool_search", decision="ALLOW", detail={"query": args.get("query"), "hits": [h["name"] for h in hits]},
                       **base)
        return _rpc(rid, {"content": [{"type": "text", "text": json.dumps(hits)}], "structuredContent": {"tools": hits}})

    # 2. Request interceptor: registry
    entry = registry["tools"].get(name)
    if not entry or entry["status"] != "active":
        records.append("denial", decision="DENY", reason="tool-not-registered", **base)
        return _denied(rid, "tool-not-registered", f"{name} is not in the registry")
    if claims.get("agent_id") not in entry["allowed_agents"]:
        records.append("denial", decision="DENY", reason="agent-not-allowed-for-tool", **base)
        return _denied(rid, "agent-not-allowed-for-tool", f"{claims.get('agent_id')} may not call {name}")
    if claims.get("scope") == "code" and name not in claims.get("tools", []):
        records.append("denial", decision="DENY", reason="tool-not-in-code-session-scope", **base)
        return _denied(rid, "tool-not-in-code-session-scope", f"{name} was not granted to this code session")

    # 2b. Live pin check
    live = await _live_tools(registry, trace_id, claims)
    if name not in live:
        records.append("denial", decision="DENY", reason="tool-pin-mismatch", **base)
        return _denied(rid, "tool-pin-mismatch", f"{name} changed since approval and is suspended")

    # 2c. Policy context
    approval_ok, approval = False, None
    if entry["tier"] == "money-moving":
        token = args.pop("approval_token", "")
        try:
            approval = verify_approval_token(token)
            approval_ok = (approval["case_id"] == case_id and approval["sub"] == claims["sub"]
                           and approval["decision"] == args.get("decision") and approval["jti"] not in USED_APPROVALS)
        except jwt.PyJWTError:
            approval_ok = False
    context = {"loan_branch": await _case_branch(case_id), "approval_token_valid": approval_ok,
               "via_code": via == "code", "case_in_scope": via != "code" or claims.get("case_id") == case_id}

    # 3. Cedar
    allowed, reasons = authorize(claims, name, context, registry)
    if not allowed:
        code = reasons[0] if reasons else "default-deny"
        records.append("denial", decision="DENY", reason=code,
                       detail={"policies": reasons, "context": context, "args": args}, **base)
        return _denied(rid, code, f"{name} denied for role {claims['role']} by: {', '.join(reasons) or 'default deny'}")

    if approval:
        USED_APPROVALS.add(approval["jti"])
        args["_approved_by"], args["_approval_jti"] = claims["sub"], approval["jti"]
    if name == "collections.log_promise_to_pay":
        args["_logged_by"] = claims["sub"]

    # Forward to the target (credentials would come from the token vault).
    server, tool = name.split(".", 1)
    result = await _upstream(server, "tools/call", {"name": tool, "arguments": args})
    if result.get("isError"):
        records.append("tool_call", decision="ERROR", reason="target-error", detail={"error": result}, **base)
        return _rpc(rid, result)

    # 4. Response interceptor
    data = result.get("structuredContent") or {}
    shaped, was_shaped = _shape(name, data, via)
    args_hash = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]
    records.append("tool_call", decision="ALLOW", reason=",".join(reasons),
                   detail={"args_hash": args_hash, "shaped": was_shaped, "result_chars": len(json.dumps(data)),
                           "returned_chars": len(json.dumps(shaped)), "result": shaped if not was_shaped else
                           {k: v for k, v in shaped.items() if k != "preview"}}, **base)
    return _rpc(rid, {"content": [{"type": "text", "text": json.dumps(shaped)}], "structuredContent": shaped,
                      "_meta": {"traceId": trace_id, "shaped": was_shaped}})


# ------------------------------------------------------------------ admin / audit
@app.get("/records")
def get_records(case_id: str | None = None, limit: int = 300):
    return records.query(case_id, limit)


@app.post("/records")
async def post_record(request: Request, x_service_token: str | None = Header(None)):
    if x_service_token != SERVICE_TOKEN:
        raise HTTPException(403, "service token required")
    body = await request.json()
    kind = body.pop("kind")
    records.append(kind, **body)
    return {"ok": True}


@app.post("/admin/reset")
def reset(x_service_token: str | None = Header(None)):
    """Demo reset: archive the decision record file (never edited in place) and start a new one."""
    if x_service_token != SERVICE_TOKEN:
        raise HTTPException(403, "service token required")
    archived = records.archive()
    USED_APPROVALS.clear()
    ALERTED_PINS.clear()
    return {"ok": True, "archived_to": archived}


@app.get("/registry")
async def get_registry():
    registry = reg.load()
    status = {}
    for server in registry["servers"]:
        for t in (await _upstream(server, "tools/list"))["tools"]:
            fq = f"{server}.{t['name']}"
            if fq in registry["tools"]:
                status[fq] = "ok" if reg.tool_hash(t) == registry["tools"][fq]["pinned_sha256"] else "PIN MISMATCH"
    return {"registry": registry, "pin_status": status, "policies": POLICY_IDS}


@app.get("/healthz")
def healthz():
    return {"ok": True}
