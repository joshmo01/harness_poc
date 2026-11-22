"""Generate synthetic lending data (deterministic, seed 42).

50 cases (1001-1050) across Pune, Nashik and Indore, MSME and retail, with
bureau summaries, 12 months of bank transactions, 18 months of repayment
history on an existing loan, and a document checklist. No real people.

Run:  python -m poc.data.generate
"""
import random
import sqlite3
from datetime import date, timedelta

from poc.common.config import DB_PATH

FIRST = ["Aarav", "Meera", "Kabir", "Sneha", "Rohan", "Priya", "Vikram", "Ananya", "Arjun", "Divya",
         "Farhan", "Kavya", "Nikhil", "Pooja", "Sanjay", "Tanvi", "Yash", "Zoya", "Harsh", "Isha"]
LAST = ["Kulkarni", "Deshmukh", "Patil", "Joshi", "Shaikh", "Jain", "Agarwal", "Rao", "Pawar", "Bhosale"]
BIZ = ["Traders", "Enterprises", "Agro Foods", "Textiles", "Auto Parts", "Pharma Distributors",
       "Kirana Mart", "Engineering Works", "Dairy Farm", "Printing Press"]
BRANCHES = ["Pune", "Nashik", "Indore"]
DOCS = ["KYC", "GST returns (12m)", "Bank statement (12m)", "ITR (2y)", "Udyam certificate", "Property papers"]

SCHEMA = """
DROP TABLE IF EXISTS cases; DROP TABLE IF EXISTS bureau; DROP TABLE IF EXISTS bank_txns;
DROP TABLE IF EXISTS repayments; DROP TABLE IF EXISTS documents; DROP TABLE IF EXISTS promises;
DROP TABLE IF EXISTS sanctions;
CREATE TABLE cases (case_id TEXT PRIMARY KEY, applicant TEXT, business TEXT, segment TEXT, product TEXT,
  branch TEXT, amount INTEGER, tenure_months INTEGER, rate REAL, emi INTEGER, monthly_income INTEGER,
  existing_emi INTEGER, stage TEXT, pan TEXT, phone TEXT, vintage_years INTEGER);
CREATE TABLE bureau (case_id TEXT PRIMARY KEY, score INTEGER, active_loans INTEGER, enquiries_6m INTEGER,
  max_dpd_24m INTEGER, written_off INTEGER);
CREATE TABLE bank_txns (case_id TEXT, txn_date TEXT, narration TEXT, amount INTEGER, kind TEXT, balance INTEGER);
CREATE TABLE repayments (case_id TEXT, month TEXT, emi_due INTEGER, paid INTEGER, dpd INTEGER);
CREATE TABLE documents (case_id TEXT, doc TEXT, status TEXT);
CREATE TABLE promises (case_id TEXT, promise_date TEXT, amount INTEGER, logged_by TEXT, logged_at TEXT);
CREATE TABLE sanctions (case_id TEXT, decision TEXT, approved_by TEXT, approved_at TEXT, approval_jti TEXT);
"""


def emi(principal: int, annual_rate: float, months: int) -> int:
    r = annual_rate / 1200
    return round(principal * r * (1 + r) ** months / ((1 + r) ** months - 1))


def build(seed: int = 42) -> None:
    rnd = random.Random(seed)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    today = date(2026, 11, 1)

    for i, cid in enumerate(range(1001, 1051)):
        case_id = str(cid)
        msme = rnd.random() < 0.6
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        branch = BRANCHES[i % 3]
        amount = rnd.choice([3, 5, 8, 12, 15, 18, 25]) * 100_000 if msme else rnd.choice([2, 4, 6, 8]) * 100_000
        tenure = rnd.choice([24, 36, 48, 60])
        rate = rnd.choice([12.5, 13.0, 14.25, 15.5, 16.0])
        income = rnd.randint(60, 400) * 1000
        existing = rnd.randint(0, 25) * 1000
        score = rnd.randint(640, 820)
        max_dpd = rnd.choice([0, 0, 0, 0, 15, 30, 60, 90])
        bounces = rnd.choice([0, 0, 0, 1, 2, 4])
        vintage = rnd.randint(1, 15)

        # Hero case for the demo: strong MSME borrower with two recent cheque bounces -> REFER.
        if case_id == "1042":
            first, last, msme, branch = "Meera", "Deshmukh", True, "Pune"
            amount, tenure, rate, income, existing = 1_800_000, 48, 14.25, 265_000, 18_000
            score, max_dpd, bounces, vintage = 758, 0, 2, 9
        # Clean approve case in Nashik (field officer's branch).
        if case_id == "1033":
            branch, score, max_dpd, bounces, income, existing = "Nashik", 790, 0, 0, 180_000, 5_000

        business = f"{last} {rnd.choice(BIZ)}" if msme else None
        e = emi(amount, rate, tenure)
        con.execute("INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            case_id, f"{first} {last}", business, "MSME" if msme else "Retail",
            "Business loan" if msme else "Personal loan", branch, amount, tenure, rate, e, income, existing,
            "Underwriting", f"{''.join(rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(5))}{rnd.randint(1000, 9999)}"
            f"{rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}", f"9{rnd.randint(100000000, 999999999)}", vintage))
        con.execute("INSERT INTO bureau VALUES (?,?,?,?,?,?)",
                    (case_id, score, rnd.randint(0, 4), rnd.randint(0, 6), max_dpd, 0))

        # 12 months of bank transactions.
        bal = rnd.randint(50, 400) * 1000
        bounce_months = set(rnd.sample(range(9, 12), k=min(bounces, 3))) if bounces else set()
        if case_id == "1042":
            bounce_months = {9, 11}
        for m in range(12):
            month_start = (today.replace(day=1) - timedelta(days=30 * (12 - m))).replace(day=1)
            credits = rnd.randint(8, 25)
            for _ in range(credits):
                amt = round(income / credits * rnd.uniform(0.6, 1.6), -2)
                bal += amt
                d = month_start + timedelta(days=rnd.randint(0, 27))
                con.execute("INSERT INTO bank_txns VALUES (?,?,?,?,?,?)",
                            (case_id, d.isoformat(), rnd.choice(["UPI/CR", "NEFT/CR", "IMPS/CR", "CASH DEP"]),
                             int(amt), "credit", int(bal)))
            for _ in range(rnd.randint(10, 30)):
                amt = round(income / 18 * rnd.uniform(0.3, 1.4), -2)
                bal = max(bal - amt, 1000)
                d = month_start + timedelta(days=rnd.randint(0, 27))
                con.execute("INSERT INTO bank_txns VALUES (?,?,?,?,?,?)",
                            (case_id, d.isoformat(), rnd.choice(["UPI/DR", "NEFT/DR", "ACH/EMI", "POS", "GST PAYMENT"]),
                             -int(amt), "debit", int(bal)))
            if m in bounce_months:
                d = month_start + timedelta(days=5)
                con.execute("INSERT INTO bank_txns VALUES (?,?,?,?,?,?)",
                            (case_id, d.isoformat(), "CHQ RETURN - INSUFFICIENT FUNDS", -590, "bounce", int(bal)))

        # 18 months of repayments on an existing loan.
        for m in range(18):
            mth = (today.replace(day=1) - timedelta(days=30 * (18 - m))).strftime("%Y-%m")
            dpd = 0
            if max_dpd and m == rnd.randint(0, 17):
                dpd = max_dpd
            due = existing or 8000
            con.execute("INSERT INTO repayments VALUES (?,?,?,?,?)", (case_id, mth, due, due if dpd == 0 else 0, dpd))

        for doc in DOCS:
            status = "Received" if rnd.random() < 0.85 or case_id == "1042" else "Pending"
            if case_id == "1042" and doc == "Property papers":
                status = "Pending"
            con.execute("INSERT INTO documents VALUES (?,?,?)", (case_id, doc, status))

    con.commit()
    n = con.execute("SELECT COUNT(*) FROM bank_txns").fetchone()[0]
    con.close()
    print(f"Wrote 50 cases and {n} bank transactions to {DB_PATH}")


if __name__ == "__main__":
    build()
