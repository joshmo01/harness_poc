"""Deterministic credit core (stub of the Yutrix UW engine).

Every number shown to a user and every recommendation comes from here, never
from the model. Each rule returns a reason code so decisions are explainable.
"""
import sqlite3
from dataclasses import asdict, dataclass

from poc.common.config import DB_PATH

RULESET_VERSION = "uw-rules-poc-1.0"


@dataclass
class RuleResult:
    code: str
    rule: str
    outcome: str  # PASS | REFER | FAIL
    detail: str


def _db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def assess(case_id: str) -> dict:
    con = _db()
    c = con.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if not c:
        raise KeyError(f"case {case_id} not found")
    b = con.execute("SELECT * FROM bureau WHERE case_id=?", (case_id,)).fetchone()
    bounces = con.execute("SELECT COUNT(*) FROM bank_txns WHERE case_id=? AND kind='bounce'", (case_id,)).fetchone()[0]
    credits = con.execute("SELECT SUM(amount) FROM bank_txns WHERE case_id=? AND kind='credit'", (case_id,)).fetchone()[0] or 0
    pending_docs = [r[0] for r in con.execute(
        "SELECT doc FROM documents WHERE case_id=? AND status='Pending'", (case_id,))]
    max_dpd = con.execute("SELECT MAX(dpd) FROM repayments WHERE case_id=?", (case_id,)).fetchone()[0] or 0
    con.close()

    foir = round((c["existing_emi"] + c["emi"]) / c["monthly_income"] * 100, 1)
    avg_monthly_credit = round(credits / 12)
    banking_ratio = round(avg_monthly_credit / c["monthly_income"] * 100)

    results = [
        RuleResult("RC-101", "Bureau score >= 700", "PASS" if b["score"] >= 700 else ("REFER" if b["score"] >= 660 else "FAIL"),
                   f"Score {b['score']}"),
        RuleResult("RC-102", "FOIR <= 55%", "PASS" if foir <= 55 else ("REFER" if foir <= 65 else "FAIL"),
                   f"FOIR {foir}% (existing EMI + proposed EMI / income)"),
        RuleResult("RC-103", "No DPD > 30 in 24 months", "PASS" if b["max_dpd_24m"] <= 30 and max_dpd <= 30 else "FAIL",
                   f"Max DPD {max(b['max_dpd_24m'], max_dpd)}"),
        RuleResult("RC-104", "Cheque bounces in 12 months <= 1", "PASS" if bounces <= 1 else ("REFER" if bounces <= 3 else "FAIL"),
                   f"{bounces} bounce(s) in bank statement"),
        RuleResult("RC-105", "Banking credits >= 80% of declared income",
                   "PASS" if banking_ratio >= 80 else "REFER", f"Banked {banking_ratio}% of declared income"),
        RuleResult("RC-106", "Bureau enquiries in 6 months <= 4", "PASS" if b["enquiries_6m"] <= 4 else "REFER",
                   f"{b['enquiries_6m']} enquiries"),
        RuleResult("RC-107", "Mandatory documents received", "PASS" if not pending_docs else "REFER",
                   "All received" if not pending_docs else "Pending: " + ", ".join(pending_docs)),
    ]
    outcomes = [r.outcome for r in results]
    recommendation = "REJECT" if "FAIL" in outcomes else ("REFER" if "REFER" in outcomes else "APPROVE")
    return {
        "case_id": case_id,
        "recommendation": recommendation,
        "ruleset_version": RULESET_VERSION,
        "metrics": {"foir_pct": foir, "bureau_score": b["score"], "bounces_12m": bounces,
                    "max_dpd": max(b["max_dpd_24m"], max_dpd), "banking_ratio_pct": banking_ratio,
                    "proposed_emi": c["emi"]},
        "reason_codes": [asdict(r) for r in results],
    }
