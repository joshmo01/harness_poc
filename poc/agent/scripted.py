"""Scripted agent: replays the turns a model would take, against live tools.

No model is called. Each scenario performs real meta-tool calls through the
gateway (so policies, pins and audit are real) and emits:

  step     - what the agent did (shown in the trace panel)
  text     - the agent's reply
  surface  - an A2UI layout with @path bindings; the BFF fills the bound
             values from the core, so no number on screen comes from the model
  context  - how much tool schema is in the agent's context

Swap `ScriptedAgent` for a Claude Agent SDK loop later: the meta-tools,
gateway client and sandbox stay the same.
"""
import re
from collections.abc import Iterator
from datetime import date, timedelta

from poc.agent.gateway_client import GatewayClient
from poc.agent.sandbox import run_code

LENDING_CATALOG_ID = "https://yutrix.io/a2ui/catalogs/lending/v1.json"

CASHFLOW_SCRIPT = """
import json
from collections import defaultdict
from yutrix_tools import los__get_bank_transactions

data = los__get_bank_transactions(case_id=CASE_ID, months=12)
months = defaultdict(lambda: {"inflow": 0, "outflow": 0, "bounces": 0, "closing": 0})
for t in data["transactions"]:
    m = months[t["txn_date"][:7]]
    if t["kind"] == "credit":
        m["inflow"] += t["amount"]
    elif t["kind"] == "bounce":
        m["bounces"] += 1
    else:
        m["outflow"] += -t["amount"]
    m["closing"] = t["balance"]
rows = [{"month": k, **v} for k, v in sorted(months.items())]
print(json.dumps({
    "transactions_analysed": data["count"],
    "avg_monthly_inflow": round(sum(r["inflow"] for r in rows) / len(rows)),
    "avg_monthly_outflow": round(sum(r["outflow"] for r in rows) / len(rows)),
    "total_bounces": sum(r["bounces"] for r in rows),
    "bounce_months": [r["month"] for r in rows if r["bounces"]],
    "lowest_closing_month": min(rows, key=lambda r: r["closing"])["month"],
    "months": rows,
}))
"""


def _case_id(text: str) -> str | None:
    m = re.search(r"\b(10[0-5]\d)\b", text)
    return m.group(1) if m else None


class ScriptedAgent:
    def __init__(self, user_token: str, user_claims: dict, trace_id: str):
        self.token, self.claims, self.trace_id = user_token, user_claims, trace_id

    # ------------------------------------------------------------ entry point
    def run(self, message: str) -> Iterator[dict]:
        msg = message.lower()
        case_id = _case_id(msg)
        gw = GatewayClient(self.token, self.trace_id, case_id)
        if not case_id:
            yield from self._help()
        elif "approve" in msg or "sanction" in msg:
            yield from self._approve_via_chat(gw, case_id)
        elif "promise" in msg or "ptp" in msg:
            yield from self._promise_to_pay(gw, case_id, message)
        elif any(w in msg for w in ("bank", "statement", "cash", "cashflow", "cash flow")):
            yield from self._cashflow(gw, case_id)
        else:
            yield from self._assess(gw, case_id)
        yield {"type": "context", **gw.context_stats(total_tools=9)}

    # ------------------------------------------------------------ helpers
    def _search(self, gw: GatewayClient, query: str) -> Iterator[dict]:
        hits = gw.search_tools(query)
        yield {"type": "step", "kind": "search", "title": f'search_tools("{query}")',
               "detail": {"found": [h["name"] for h in hits]}}
        self._last_hits = [h["name"] for h in hits]

    def _schemas(self, gw: GatewayClient, names: list[str]) -> Iterator[dict]:
        got = gw.get_tool_schema(names)
        yield {"type": "step", "kind": "schema", "title": f"get_tool_schema({len(got)} tools)",
               "detail": {"loaded": list(got)}}
        self._available = set(got)

    def _call(self, gw: GatewayClient, name: str, args: dict) -> Iterator[dict]:
        r = gw.call_tool(name, args)
        d = r["data"]
        if r["ok"]:
            yield {"type": "step", "kind": "tool", "title": f"{name}", "detail": {"args": args, "result_chars": r["chars"],
                                                                                  "shaped": "note" in d}}
        else:
            yield {"type": "step", "kind": "deny", "title": f"{name} denied",
                   "detail": {"reason_code": d.get("reason_code"), "message": d.get("message") or d.get("error")}}
        self._last = r

    # ------------------------------------------------------------ scenarios
    def _help(self) -> Iterator[dict]:
        yield {"type": "text", "text": "Tell me which case to work on, for example: \"Assess case 1042\", "
               "\"Analyse the bank statement for case 1042\", \"Log a promise to pay for case 1033\" "
               "or \"Approve case 1042\"."}

    def _assess(self, gw: GatewayClient, case_id: str) -> Iterator[dict]:
        yield {"type": "step", "kind": "info", "title": "Plan",
               "detail": {"text": f"Assess case {case_id}: find tools for case data, credit rules, bureau and repayments."}}
        yield from self._search(gw, "case summary loan application")
        yield from self._search(gw, "credit rules recommendation reason codes")
        yield from self._search(gw, "bureau score repayment history documents")
        wanted = ["los.get_case_summary", "core.assess_case", "bureau.get_bureau_summary",
                  "lms.get_repayment_history", "los.list_documents"]
        yield from self._schemas(gw, wanted)

        results = {}
        for name in wanted:
            if name not in self._available:
                yield {"type": "step", "kind": "deny", "title": f"{name} unavailable",
                       "detail": {"reason_code": "not-visible", "message": "Not returned by the gateway (suspended or not permitted)."}}
                continue
            yield from self._call(gw, name, {"case_id": case_id})
            results[name] = self._last
            if not self._last["ok"] and self._last["data"].get("reason_code") == "field-officer-own-branch-only":
                yield {"type": "text", "text": f"I can't open case {case_id}: policy **field-officer-own-branch-only** "
                       f"allows {self.claims['name']} to see only {self.claims['branch']} branch cases. "
                       "The denial is in the decision record."}
                yield {"type": "surface", "surface_id": f"denied-{case_id}-{self.trace_id}", "components": [
                    {"id": "root", "component": "AccessNotice", "title": f"Case {case_id} not available",
                     "code": "field-officer-own-branch-only",
                     "message": f"Your role can only open {self.claims['branch']} branch cases."}], "bindings": {}}
                return

        assessment = results.get("core.assess_case", {}).get("data", {})
        rec = assessment.get("recommendation", "UNKNOWN")
        flags = [r for r in assessment.get("reason_codes", []) if r["outcome"] != "PASS"]
        bureau_ok = results.get("bureau.get_bureau_summary", {}).get("ok", False)

        children = ["hdr", "rec", "summary", "codes"]
        components = [
            {"id": "hdr", "component": "Heading", "text": f"Case {case_id} · Underwriting review"},
            {"id": "rec", "component": "RecommendationBanner", "recommendation": {"@path": "/assessment/recommendation"},
             "ruleset": {"@path": "/assessment/ruleset_version"}, "metrics": {"@path": "/assessment/metrics"}},
            {"id": "summary", "component": "CaseSummaryCard", "case": {"@path": "/case"}},
            {"id": "codes", "component": "ReasonCodeList", "items": {"@path": "/assessment/reason_codes"}},
        ]
        bindings = {"/case": {"tool": "los.get_case_summary", "args": {"case_id": case_id}},
                    "/assessment": {"tool": "core.assess_case", "args": {"case_id": case_id}},
                    "/repayments": {"tool": "lms.get_repayment_history", "args": {"case_id": case_id}},
                    "/documents": {"tool": "los.list_documents", "args": {"case_id": case_id}}}
        if bureau_ok:
            children.append("bureau")
            components.append({"id": "bureau", "component": "BureauSummary", "bureau": {"@path": "/bureau"}})
            bindings["/bureau"] = {"tool": "bureau.get_bureau_summary", "args": {"case_id": case_id}}
        else:
            children.append("bureau_note")
            components.append({"id": "bureau_note", "component": "AccessNotice", "title": "Bureau data withheld",
                               "code": "tool-pin-mismatch",
                               "message": "The bureau tool changed since it was approved, so the gateway suspended it."})
        children += ["dpd", "docs", "approval"]
        components += [
            {"id": "dpd", "component": "DpdTrend", "history": {"@path": "/repayments/history"}},
            {"id": "docs", "component": "DocumentChecklist", "documents": {"@path": "/documents/documents"}},
            {"id": "approval", "component": "ApprovalPanel", "caseId": case_id,
             "recommendation": {"@path": "/assessment/recommendation"},
             "onApprove": {"event": {"name": "approve_sanction", "context": {"case_id": case_id, "decision": "APPROVE"}}},
             "onReject": {"event": {"name": "approve_sanction", "context": {"case_id": case_id, "decision": "REJECT"}}}},
        ]
        components.insert(0, {"id": "root", "component": "Stack", "children": children})

        reasons = "; ".join(f"**{r['code']}** {r['rule']} ({r['detail']})" for r in flags) or "all rules passed"
        text = f"The credit rules recommend **{rec}** for case {case_id}. Points to review: {reasons}."
        if not bureau_ok:
            text += " Bureau data is missing because the gateway suspended the bureau tool after its definition changed."
        if rec != "REJECT":
            text += " A credit manager can approve or reject using the panel; I can't do that for you."
        yield {"type": "text", "text": text}
        yield {"type": "surface", "surface_id": f"case-{case_id}-{self.trace_id}", "components": components,
               "bindings": bindings}

    def _cashflow(self, gw: GatewayClient, case_id: str) -> Iterator[dict]:
        yield from self._search(gw, "bank statement transactions")
        yield from self._schemas(gw, ["los.get_bank_transactions"])
        yield from self._call(gw, "los.get_bank_transactions", {"case_id": case_id, "months": 12})
        if not self._last["ok"]:
            yield {"type": "text", "text": f"I couldn't get the bank statement: {self._last['data'].get('message')}"}
            return
        n = self._last["data"].get("count")
        yield {"type": "step", "kind": "info", "title": "Too large for context",
               "detail": {"text": f"Gateway withheld {n} rows from my context. Switching to code mode."}}
        script = CASHFLOW_SCRIPT.replace("CASE_ID", repr(case_id))
        res = run_code(script, user_claims=self.claims, case_id=case_id, tools=["los.get_bank_transactions"],
                       trace_id=self.trace_id)
        yield {"type": "step", "kind": "code", "title": "run_code (Code Interpreter stand-in)",
               "detail": {"script": script.strip(), "sdk": res.get("sdk_functions"), "ok": res["ok"],
                          "output_chars": len(res["stdout"]), "stderr": res["stderr"][-400:]}}
        out = res.get("json")
        if not out:
            yield {"type": "text", "text": "The analysis script failed; see the trace for details."}
            return
        yield {"type": "text", "text": (
            f"I analysed {out['transactions_analysed']} transactions in code; only a {len(res['stdout'])}-character "
            f"summary came back to me. Average monthly inflow is ₹{out['avg_monthly_inflow']:,} against outflow of "
            f"₹{out['avg_monthly_outflow']:,}. There were {out['total_bounces']} cheque bounce(s)"
            + (f" in {', '.join(out['bounce_months'])}." if out["bounce_months"] else "."))}
        yield {"type": "surface", "surface_id": f"cash-{case_id}-{self.trace_id}", "components": [
            {"id": "root", "component": "Stack", "children": ["hdr", "cash"]},
            {"id": "hdr", "component": "Heading", "text": f"Case {case_id} · 12-month cash flow"},
            {"id": "cash", "component": "CashflowSummary", "summary": {"@path": "/cashflow"}},
        ], "bindings": {}, "data": {"/cashflow": {**out, "source": "code mode over los.get_bank_transactions"}}}

    def _promise_to_pay(self, gw: GatewayClient, case_id: str, message: str) -> Iterator[dict]:
        amount = re.search(r"(?:₹|rs\.?|inr)?\s?(\d{4,7})(?!\d)", message.replace(",", "").replace(case_id, ""))
        amount = int(amount.group(1)) if amount else 15000
        when = re.search(r"(20\d\d-\d\d-\d\d)", message)
        when = when.group(1) if when else (date.today() + timedelta(days=7)).isoformat()
        yield from self._search(gw, "promise to pay overdue collections")
        yield from self._schemas(gw, ["collections.log_promise_to_pay", "collections.get_dpd_summary"])
        if "collections.log_promise_to_pay" not in self._available:
            yield {"type": "text", "text": "Logging a promise to pay isn't available for your role."}
            return
        yield from self._call(gw, "collections.log_promise_to_pay",
                              {"case_id": case_id, "promise_date": when, "amount": amount})
        if not self._last["ok"]:
            d = self._last["data"]
            yield {"type": "text", "text": f"The gateway denied it: **{d.get('reason_code')}**. {d.get('message', '')}"}
            return
        yield {"type": "text", "text": f"Logged a promise to pay of ₹{amount:,} on {when} for case {case_id}."}
        yield {"type": "surface", "surface_id": f"ptp-{case_id}-{self.trace_id}", "components": [
            {"id": "root", "component": "Stack", "children": ["hdr", "dpd"]},
            {"id": "hdr", "component": "Heading", "text": f"Case {case_id} · Collections"},
            {"id": "dpd", "component": "CollectionsCard", "summary": {"@path": "/collections"}},
        ], "bindings": {"/collections": {"tool": "collections.get_dpd_summary", "args": {"case_id": case_id}}}}

    def _approve_via_chat(self, gw: GatewayClient, case_id: str) -> Iterator[dict]:
        yield from self._search(gw, "approve sanction decision")
        found = "los.approve_sanction" in self._last_hits
        yield {"type": "step", "kind": "info", "title": "Approval tool not offered to the agent" if not found else "Found",
               "detail": {"text": "Money-moving tools are registered for the BFF only, never for an agent."}}
        # Try anyway, to show the gateway refuses even a direct attempt.
        yield from self._call(gw, "los.approve_sanction", {"case_id": case_id, "decision": "APPROVE",
                                                           "approval_token": ""})
        code = self._last["data"].get("reason_code", "denied")
        yield {"type": "text", "text": (
            f"I can't approve case {case_id}. The gateway refused my attempt (**{code}**): sanctions need a human "
            "click on the Approve button, which mints a one-time approval token outside the agent. "
            f"Ask me to \"assess case {case_id}\" to open the approval panel.")}
