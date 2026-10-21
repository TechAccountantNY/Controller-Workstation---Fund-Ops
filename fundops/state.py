"""SQLite state store: documents, arrivals, exceptions and the decision log.

The decision log is append-only. A re-run of the pipeline refreshes the
documents/arrivals/exceptions tables but never touches decisions; an exception
whose key still exists keeps its disposition, one that disappears is closed as
'resolved_by_data'. That is the human-in-the-loop contract: the machine
proposes, the person disposes, and the record survives re-ingestion."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import STATE_DB

DISPOSITIONS = ("APPROVE", "REJECT", "WAIVE", "ESCALATE", "REQUEST_INFO", "NOTE")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, ran_at TEXT NOT NULL, period TEXT NOT NULL,
  n_messages INTEGER, n_exceptions INTEGER, assumptions_json TEXT);
CREATE TABLE IF NOT EXISTS documents (
  msg_id TEXT PRIMARY KEY, filename TEXT, sent TEXT, sender TEXT, subject TEXT, category TEXT,
  relevance TEXT, classification_rule TEXT, counterparty TEXT, attachment TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS arrivals (
  item TEXT PRIMARY KEY, kind TEXT, counterparty TEXT, expected_by TEXT, received_on TEXT, document TEXT, status TEXT, quality TEXT);
CREATE TABLE IF NOT EXISTS exceptions (
  key TEXT PRIMARY KEY, rank INTEGER, severity TEXT, category TEXT, area TEXT, title TEXT, detail TEXT,
  amount_impact_usd REAL, evidence TEXT, recommended_action TEXT, decision_required INTEGER, counterparty TEXT,
  findings TEXT, first_seen_run INTEGER, last_seen_run INTEGER, status TEXT NOT NULL DEFAULT 'open');
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, exception_key TEXT NOT NULL, disposition TEXT NOT NULL,
  decided_by TEXT NOT NULL, decided_at TEXT NOT NULL, rationale TEXT NOT NULL, run_id INTEGER);
CREATE INDEX IF NOT EXISTS ix_dec_key ON decisions(exception_key, id);
"""


def connect(path: Path = STATE_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_run(con: sqlite3.Connection, result, arrivals: pd.DataFrame) -> int:
    ctx = result.ctx
    cur = con.execute("INSERT INTO runs(ran_at, period, n_messages, n_exceptions, assumptions_json) VALUES (?,?,?,?,?)",
                      (_now(), ctx.period, len(ctx.messages), len(result.checks.exceptions), json.dumps(ctx.assumptions.__dict__)))
    run_id = cur.lastrowid

    con.execute("DELETE FROM documents")
    con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [(m.msg_id, m.filename, m.sent.isoformat(), m.sender, m.subject, m.category, m.relevance,
                      m.classification_rule, m.counterparty, m.attachment, m.body.strip()) for m in ctx.messages])

    con.execute("DELETE FROM arrivals")
    con.executemany("INSERT INTO arrivals VALUES (?,?,?,?,?,?,?,?)",
                    [(r.item, r.kind, r.counterparty, str(r.expected_by or ""), str(r.received_on or ""), r.document, r.status, r.quality)
                     for r in arrivals.itertuples()])

    seen = set()
    for e in result.checks.exceptions:
        seen.add(e.key)
        con.execute("""INSERT INTO exceptions(key, rank, severity, category, area, title, detail, amount_impact_usd, evidence,
                       recommended_action, decision_required, counterparty, findings, first_seen_run, last_seen_run, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')
                       ON CONFLICT(key) DO UPDATE SET rank=excluded.rank, severity=excluded.severity, category=excluded.category,
                       area=excluded.area, title=excluded.title, detail=excluded.detail, amount_impact_usd=excluded.amount_impact_usd,
                       evidence=excluded.evidence, recommended_action=excluded.recommended_action, decision_required=excluded.decision_required,
                       counterparty=excluded.counterparty, findings=excluded.findings, last_seen_run=excluded.last_seen_run,
                       status=CASE WHEN exceptions.status='resolved_by_data' THEN 'open' ELSE exceptions.status END""",
                    (e.key, e.rank, e.severity, e.category, e.area, e.title, e.detail, e.amount_impact_usd, json.dumps(e.evidence),
                     e.recommended_action, int(e.decision_required), e.counterparty, json.dumps(e.findings), run_id, run_id))
    for row in con.execute("SELECT key FROM exceptions WHERE status='open'").fetchall():
        if row["key"] not in seen:
            con.execute("UPDATE exceptions SET status='resolved_by_data', last_seen_run=? WHERE key=?", (run_id, row["key"]))
    con.commit()
    return run_id


def record_decision(con: sqlite3.Connection, key: str, disposition: str, by: str, rationale: str, run_id: int | None = None) -> int:
    disposition = disposition.upper()
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    if not rationale.strip():
        raise ValueError("a rationale is required — a disposition without a reason is not a control")
    if con.execute("SELECT 1 FROM exceptions WHERE key=?", (key,)).fetchone() is None:
        raise KeyError(f"unknown exception key {key!r}")
    cur = con.execute("INSERT INTO decisions(exception_key, disposition, decided_by, decided_at, rationale, run_id) VALUES (?,?,?,?,?,?)",
                      (key, disposition, by, _now(), rationale.strip(), run_id))
    con.execute("UPDATE exceptions SET status=? WHERE key=?", ("open" if disposition in ("REQUEST_INFO", "ESCALATE", "NOTE") else "dispositioned", key))
    con.commit()
    return cur.lastrowid


def exceptions_with_decisions(con: sqlite3.Connection) -> pd.DataFrame:
    ex = pd.read_sql_query("SELECT * FROM exceptions ORDER BY rank", con)
    dec = pd.read_sql_query("""SELECT d.* FROM decisions d JOIN (SELECT exception_key, MAX(id) AS id FROM decisions GROUP BY exception_key) m
                               ON d.id = m.id""", con)
    counts = pd.read_sql_query("SELECT exception_key, COUNT(*) AS n_decisions FROM decisions GROUP BY exception_key", con)
    out = ex.merge(dec.rename(columns={"exception_key": "key", "id": "decision_id"}), on="key", how="left") \
            .merge(counts.rename(columns={"exception_key": "key"}), on="key", how="left")
    out["n_decisions"] = out["n_decisions"].fillna(0).astype(int)
    out["disposition"] = out["disposition"].fillna("")
    for c in ("evidence", "findings"):
        out[c] = out[c].apply(lambda s: json.loads(s) if isinstance(s, str) and s.startswith("[") else [])
    return out


def decision_log(con: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("""SELECT d.id, d.exception_key, e.rank, e.severity, e.title, d.disposition, d.decided_by, d.decided_at, d.rationale
                                FROM decisions d LEFT JOIN exceptions e ON e.key = d.exception_key ORDER BY d.id""", con)


def reset_decisions(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM decisions")
    con.execute("UPDATE exceptions SET status='open' WHERE status='dispositioned'")
    con.commit()


def seed_decisions(con: sqlite3.Connection, seed_path: Path) -> int:
    seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    n = 0
    for s in seeds["decisions"]:
        try:
            record_decision(con, s["exception_key"], s["disposition"], seeds["decided_by"], s["rationale"])
            n += 1
        except KeyError:
            print(f"  seed skipped — no such exception this run: {s['exception_key']}")
    return n
