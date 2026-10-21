"""The human-in-the-loop decision log.

A plain JSON file, committed to git, so the audit trail is diffable and readable
without the app. The system never changes a NAV treatment on its own: it proposes
a default, and the roll-forward uses the default only until a human records a
decision. Every decision carries who, when (real clock), the scenario date, the
disposition and the rationale.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import asdict
from pathlib import Path

from .config import AS_OF, DECISIONS_FILE
from .models import Decision

DISPOSITIONS = ["APPROVE", "REJECT", "WAIVE", "ESCALATE", "REQUEST_INFO"]


def load_decisions(path: Path = DECISIONS_FILE) -> list[Decision]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Decision(**d) for d in raw.get("decisions", [])]


def save_decisions(decisions: list[Decision], path: Path = DECISIONS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"_about": "Append-only decision log for the Controller Workstation — Fund Ops module. "
                         "Latest decision per exception wins. Edit via the app or by hand; it is plain JSON on purpose.",
               "decisions": [asdict(d) for d in decisions]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record(exc_id: str, disposition: str, treatment: str | None, rationale: str, decided_by: str,
           path: Path = DECISIONS_FILE) -> Decision:
    if disposition not in DISPOSITIONS:
        raise ValueError(f"disposition must be one of {DISPOSITIONS}")
    d = Decision(exc_id=exc_id, disposition=disposition, treatment=treatment or None, rationale=rationale.strip(),
                 decided_by=decided_by.strip(), decided_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                 scenario_date=AS_OF.isoformat())
    ds = load_decisions(path)
    ds.append(d)
    save_decisions(ds, path)
    return d


def latest_by_exception(decisions: list[Decision]) -> dict[str, Decision]:
    out: dict[str, Decision] = {}
    for d in decisions:  # file order is chronological; last one wins
        out[d.exc_id] = d
    return out


def effective_treatments(exceptions, decisions: list[Decision]) -> dict[str, str]:
    """Default treatment per exception, overridden by the latest human decision that names one."""
    latest = latest_by_exception(decisions)
    t: dict[str, str] = {}
    for x in exceptions:
        if x.default_treatment:
            t[x.exc_id] = x.default_treatment
        d = latest.get(x.exc_id)
        if d and d.treatment and d.treatment in x.treatments:
            t[x.exc_id] = d.treatment
    return t
