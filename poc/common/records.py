"""Decision record: append-only event log per case.

POC: SQLite. Production: S3 with Object Lock (compliance mode) + a DynamoDB
index, written by the gateway's response interceptor and the BFF.
"""
import json
import sqlite3
import threading
from datetime import datetime, timezone

from poc.common.config import AUDIT_DB_PATH

_lock = threading.Lock()


def _con():
    con = sqlite3.connect(AUDIT_DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, case_id TEXT, trace_id TEXT, kind TEXT,
        actor TEXT, role TEXT, agent TEXT, tool TEXT, via TEXT, decision TEXT, reason TEXT, detail TEXT)""")
    # Append-only: block updates and deletes at the database level.
    con.execute("CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END")
    con.execute("CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'append-only'); END")
    return con


MAX_DETAIL = 20000


def _detail_json(detail: dict | None) -> str:
    text = json.dumps(detail or {}, default=str)
    if len(text) > MAX_DETAIL:
        text = json.dumps({"truncated": True, "preview": text[:MAX_DETAIL - 200]})
    return text


def append(kind: str, *, case_id: str | None = None, trace_id: str | None = None, actor: str | None = None,
           role: str | None = None, agent: str | None = None, tool: str | None = None, via: str | None = None,
           decision: str | None = None, reason: str | None = None, detail: dict | None = None) -> None:
    with _lock:
        con = _con()
        con.execute("INSERT INTO events (ts, case_id, trace_id, kind, actor, role, agent, tool, via, decision, reason, detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (datetime.now(timezone.utc).isoformat(timespec="milliseconds"), case_id, trace_id, kind, actor,
                     role, agent, tool, via, decision, reason, _detail_json(detail)))
        con.commit()
        con.close()


def query(case_id: str | None = None, limit: int = 300) -> list[dict]:
    con = _con()
    if case_id:
        rows = con.execute("SELECT * FROM events WHERE case_id=? ORDER BY id DESC LIMIT ?", (case_id, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d["detail"] or "{}")
        except ValueError:
            d["detail"] = {"unparsed": d["detail"][:500]}
        out.append(d)
    return out


def archive() -> str | None:
    """Move the current record file aside (demo reset). Production records are never removed."""
    with _lock:
        if not AUDIT_DB_PATH.exists():
            return None
        target = AUDIT_DB_PATH.with_name(f"records-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.db")
        AUDIT_DB_PATH.rename(target)
        return target.name
