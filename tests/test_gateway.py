"""Gateway governance tests: run against in-process apps (no network ports).

    pytest -q
"""
import json
import os
import tempfile

import pytest

os.environ.setdefault("POC_DATA_DIR", tempfile.mkdtemp(prefix="poc-test-"))

from fastapi.testclient import TestClient  # noqa: E402

from poc.common import tokens  # noqa: E402
from poc.data.generate import build  # noqa: E402
from poc.gateway import app as gw  # noqa: E402
from poc.gateway import registry  # noqa: E402
from poc.stubs import servers  # noqa: E402

stubs_client = TestClient(servers.app)


class _StubTransport:
    """Route the gateway's outbound httpx calls to the in-process stub app."""

    async def post(self, url, headers=None, json=None):
        path = url.split("8101", 1)[-1] if "8101" in url else url
        r = stubs_client.post(path, headers=headers, json=json)
        r.raise_for_status = lambda: None
        return r

    async def get(self, url):
        path = url.split("8101", 1)[-1] if "8101" in url else url
        return stubs_client.get(path)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.fixture(autouse=True, scope="module")
def setup():
    build()
    registry.pin()
    gw.httpx.AsyncClient = lambda **kw: _StubTransport()  # type: ignore[assignment]
    yield


client = TestClient(gw.app)


def call(persona, name, args, agent_id="uw-agent", token=None):
    token = token or tokens.issue_user_token(persona, agent_id=agent_id)
    r = client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    return r.json()["result"]


def reason(result):
    return (result.get("structuredContent") or {}).get("reason_code")


def test_read_allowed_for_underwriter():
    r = call("underwriter", "core.assess_case", {"case_id": "1042"})
    assert not r.get("isError")
    assert r["structuredContent"]["recommendation"] in {"APPROVE", "REFER", "REJECT"}


def test_field_officer_limited_to_own_branch():
    assert reason(call("field_officer", "los.get_case_summary", {"case_id": "1042"})) == "field-officer-own-branch-only"
    assert not call("field_officer", "los.get_case_summary", {"case_id": "1033"}).get("isError")


def test_agent_can_never_call_money_moving_tool():
    assert reason(call("credit_manager", "los.approve_sanction",
                       {"case_id": "1042", "decision": "APPROVE", "approval_token": ""})) == "agent-not-allowed-for-tool"


def test_approval_needs_valid_single_use_token():
    cm = {**tokens.PERSONAS["credit_manager"]}
    good = tokens.mint_approval_token(cm, "1042", "APPROVE")
    args = {"case_id": "1042", "decision": "APPROVE"}
    assert reason(call("credit_manager", "los.approve_sanction", {**args, "approval_token": "bad"},
                       agent_id="lending-bff")) == "money-moving-needs-human-approval"
    assert not call("credit_manager", "los.approve_sanction", {**args, "approval_token": good},
                    agent_id="lending-bff").get("isError")
    # replay of the same token is refused
    assert reason(call("credit_manager", "los.approve_sanction", {**args, "approval_token": good},
                       agent_id="lending-bff")) == "money-moving-needs-human-approval"


def test_underwriter_cannot_approve_even_with_token():
    uw = {**tokens.PERSONAS["underwriter"]}
    t = tokens.mint_approval_token(uw, "1042", "APPROVE")
    r = call("underwriter", "los.approve_sanction", {"case_id": "1042", "decision": "APPROVE", "approval_token": t},
             agent_id="lending-bff")
    assert r.get("isError")


def test_unregistered_agent_rejected():
    t = tokens.issue_user_token("underwriter", agent_id="rogue-agent")
    r = client.post("/mcp", headers={"Authorization": f"Bearer {t}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 403


def test_large_result_is_shaped_for_agent():
    r = call("underwriter", "los.get_bank_transactions", {"case_id": "1042"})
    sc = r["structuredContent"]
    assert "transactions" not in sc and sc["count"] > 100


def test_code_token_bound_to_case_and_read_only():
    claims = {**tokens.PERSONAS["underwriter"], "agent_id": "uw-agent"}
    t = tokens.issue_code_token(claims, "1042", ["los.get_bank_transactions"])
    ok = call(None, "los.get_bank_transactions", {"case_id": "1042"}, token=t)
    assert len(ok["structuredContent"]["transactions"]) > 100  # code mode gets full data
    assert reason(call(None, "los.get_bank_transactions", {"case_id": "1033"}, token=t)) == "code-token-bound-to-one-case"
    assert reason(call(None, "core.assess_case", {"case_id": "1042"}, token=t)) == "tool-not-in-code-session-scope"


def test_tampered_tool_is_hidden_and_blocked():
    servers.TAMPER["enabled"] = True
    try:
        t = tokens.issue_user_token("underwriter")
        listed = client.post("/mcp", headers={"Authorization": f"Bearer {t}"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
        assert "bureau.get_bureau_summary" not in {x["name"] for x in listed}
        assert reason(call("underwriter", "bureau.get_bureau_summary", {"case_id": "1042"})) == "tool-pin-mismatch"
    finally:
        servers.TAMPER["enabled"] = False


def test_every_decision_is_recorded():
    rows = client.get("/records", params={"case_id": "1042"}).json()
    kinds = {r["kind"] for r in rows}
    assert {"tool_call", "denial"} <= kinds
    assert all(json.dumps(r["detail"]) for r in rows)
