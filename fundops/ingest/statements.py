"""Manager statement parsers — one per format the inbox actually contains.

Formats seen this month: CSV, XLSX, figures in an email body, and a fixed-width
text file from an "automated rendering" system. Each parser returns a
``ManagerStatement`` with provenance (document, method, confidence) and keeps the
raw return string, because the unit a manager uses for "net return" is not
reliable: ``1.42%``, ``0.0207`` and ``2.04`` all appeared this month meaning
1.42%, 2.07% and 2.04%.

The unit is therefore *inferred by reconciling to the NAV movement* — the
interpretation (percent vs decimal) that lands within tolerance of
closing/opening − 1 wins, and the inference is recorded. If neither
interpretation reconciles, the statement is still captured and the arithmetic
check downstream raises the exception; nothing is silently "fixed".
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

import openpyxl
import pandas as pd

from ..config import ATTACH_DIR, PERIOD
from ..models import ManagerStatement, Message
from .reference import MANAGER_DOMAINS

_NUM = r"-?[\d,]+(?:\.\d+)?"


_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_MONTH_NAMES = {"january": "Jan", "february": "Feb", "march": "Mar", "april": "Apr", "may": "May",
                "june": "Jun", "july": "Jul", "august": "Aug", "september": "Sep", "sept": "Sep",
                "october": "Oct", "november": "Nov", "december": "Dec"}


def detect_period(text: str, default: str = PERIOD) -> str:
    """Find a 'Mon-YYYY' period in free text ('August 2026', 'Sep-26', 'SEPTEMBER2026')."""
    t = text.lower()
    m = re.search(r"(january|february|march|april|may|june|july|august|september|sept|october|november|december|"
                  r"jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[\s\-]*(20\d\d|\d\d)\b", t)
    if not m:
        return default
    mon = _MONTH_NAMES.get(m.group(1), m.group(1).capitalize())
    yr = m.group(2) if len(m.group(2)) == 4 else "20" + m.group(2)
    return f"{mon}-{yr}"


def _to_float(s) -> Optional[float]:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(",", "").replace("$", "").replace("USD", "").strip()
    if s == "":
        return None
    return float(s)


def infer_return_pct(raw, opening: float, closing: float, tol_pct: float = 0.005) -> tuple[float, str]:
    """Return (return in percent, unit label). See module docstring."""
    raw_s = str(raw).strip()
    if raw_s.endswith("%"):
        return float(raw_s[:-1].replace(",", "")), "percent_string"
    if raw_s.lower().endswith("pct"):
        return float(raw_s[:-3].strip().replace(",", "")), "percent_string"
    v = float(raw_s.replace(",", ""))
    implied = (closing / opening - 1.0) * 100.0 if opening else None
    if implied is None:
        return v, "percent_assumed"
    as_percent = abs(v - implied)
    as_decimal = abs(v * 100.0 - implied)
    if as_decimal < as_percent and as_decimal <= tol_pct:
        return v * 100.0, "decimal"
    if as_percent <= tol_pct:
        return v, "percent"
    # Neither reconciles — keep the more plausible reading and let the arithmetic check flag it.
    return (v * 100.0, "decimal_unreconciled") if abs(v) < 0.5 else (v, "percent_unreconciled")


# ---------------------------------------------------------------- CSV ----------
def parse_csv_statement(path: Path, msg: Message) -> ManagerStatement:
    df = pd.read_csv(path)
    row = df.iloc[0]
    opening, closing = _to_float(row["opening_nav_usd"]), _to_float(row["closing_nav_usd"])
    ret, unit = infer_return_pct(row["net_return"], opening, closing)
    return ManagerStatement(
        sleeve=str(row["sleeve"]).strip(), period=str(row["period"]).strip(),
        opening_nav=opening, return_pct=ret, closing_nav=closing,
        management_fee=_to_float(row.get("management_fee_usd")),
        incentive_fee=_to_float(row.get("incentive_fee_usd")),
        source_doc=path.name, received=msg.sent, extraction_method="csv", confidence="high",
        return_raw=str(row["net_return"]), return_unit_inferred=unit,
        status_label=_status_label(msg), raw=row.to_dict(),
    )


# --------------------------------------------------------------- XLSX ----------
_XLSX_HEADER_ALIASES = {
    "opening": ("opening nav", "open nav"),
    "return": ("net return", "return"),
    "closing": ("closing nav", "close nav"),
    "mgmt": ("management fee", "mgmt fee"),
    "incentive": ("incentive",),
    "notes": ("notes",),
    "period": ("period", "month"),
}


def parse_xlsx_statement(path: Path, msg: Message, sleeve: str) -> ManagerStatement:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    header_idx, colmap = None, {}
    for i, r in enumerate(rows):
        lowered = [str(c).lower() if c is not None else "" for c in r]
        found = {}
        for key, aliases in _XLSX_HEADER_ALIASES.items():
            for j, cell in enumerate(lowered):
                if any(cell.startswith(a) for a in aliases):
                    found[key] = j
                    break
        if {"opening", "closing", "return"} <= found.keys():
            header_idx, colmap = i, found
            break
    if header_idx is None:
        raise ValueError(f"{path.name}: no header row with opening/closing/return columns")
    data = next(r for r in rows[header_idx + 1:] if any(c is not None for c in r))
    get = lambda k: data[colmap[k]] if k in colmap and colmap[k] < len(data) else None  # noqa: E731
    opening, closing = _to_float(get("opening")), _to_float(get("closing"))
    ret, unit = infer_return_pct(get("return"), opening, closing)
    title_lines = [str(c) for r in rows[:header_idx] for c in r if c is not None]
    return ManagerStatement(
        sleeve=sleeve, period=detect_period(str(get("period") or ""), detect_period(" ".join(title_lines) + " " + msg.subject)),
        opening_nav=opening, return_pct=ret, closing_nav=closing,
        management_fee=_to_float(get("mgmt")), incentive_fee=_to_float(get("incentive")),
        source_doc=path.name, received=msg.sent, extraction_method="xlsx", confidence="high",
        return_raw=str(get("return")), return_unit_inferred=unit,
        notes=str(get("notes") or ""), status_label=_status_label(msg, " ".join(title_lines)),
        raw={"header": [rows[header_idx]], "data": data, "title": title_lines},
    )


# --------------------------------------------------------- email body ----------
_EMAIL_PATTERNS = {
    "opening": r"opening nav\s*:?\s*\$?\s*(" + _NUM + ")",
    "return": r"net return\s*:?\s*(" + _NUM + r"\s*%?)",
    "closing": r"closing nav\s*:?\s*\$?\s*(" + _NUM + ")",
    "mgmt": r"management fee(?: accrued)?\s*:?\s*\$?\s*(" + _NUM + ")",
    "incentive": r"incentive fee(?: accrued)?\s*:?\s*\$?\s*(" + _NUM + ")",
}


def parse_email_body_statement(msg: Message, sleeve: str) -> ManagerStatement:
    body = msg.body
    got = {}
    for k, pat in _EMAIL_PATTERNS.items():
        m = re.search(pat, body, re.I)
        got[k] = m.group(1).strip() if m else None
    missing = [k for k in ("opening", "return", "closing") if got[k] is None]
    if missing:
        raise ValueError(f"{msg.filename}: could not find {missing} in email body")
    opening, closing = _to_float(got["opening"]), _to_float(got["closing"])
    ret, unit = infer_return_pct(got["return"], opening, closing)
    # Keep any parenthetical the manager wrote next to a fee — it is often the tell.
    inc_note = re.search(r"incentive fee[^\n]*\(([^)]+)\)", body, re.I)
    notes = inc_note.group(1).strip() if inc_note else ""
    follow = re.search(r"(formal statement[^\n.]*)", body, re.I)
    if follow:
        notes = (notes + "; " if notes else "") + follow.group(1).strip()
    return ManagerStatement(
        sleeve=sleeve, period=detect_period(msg.subject + " " + body), opening_nav=opening, return_pct=ret, closing_nav=closing,
        management_fee=_to_float(got["mgmt"]), incentive_fee=_to_float(got["incentive"]),
        source_doc=msg.filename, received=msg.sent, extraction_method="email_body", confidence="medium",
        return_raw=got["return"], return_unit_inferred=unit, notes=notes,
        status_label=_status_label(msg), raw=got,
    )


# ---------------------------------------------------- fixed-width text ----------
_FW_PATTERNS = {
    "opening": r"OPENING BALANCE\s*(" + _NUM + r")\s*USD",
    "return": r"NET PERFORMANCE\s*(" + _NUM + r")\s*PCT",
    "mgmt": r"MGMT FEE ACCRUAL\s*(" + _NUM + r")\s*USD",
    "incentive": r"INCENTIVE ACCRUAL\s*(" + _NUM + r")\s*USD",
    "closing": r"CLOSING BALANCE\s*(" + _NUM + r")\s*USD",
}


def parse_fixed_width_statement(path: Path, msg: Message, sleeve: str) -> ManagerStatement:
    text = path.read_text(encoding="utf-8")
    got = {}
    for k, pat in _FW_PATTERNS.items():
        m = re.search(pat, text, re.I)
        got[k] = m.group(1) if m else None
    missing = [k for k in ("opening", "return", "closing") if got[k] is None]
    if missing:
        raise ValueError(f"{path.name}: could not find {missing}")
    opening, closing = _to_float(got["opening"]), _to_float(got["closing"])
    ret, unit = infer_return_pct(got["return"] + " PCT", opening, closing)
    caveat = re.search(r"(THIS DOCUMENT WAS PRODUCED[^\n]*\n[^\n]*)", text)
    return ManagerStatement(
        sleeve=sleeve, period=detect_period(text, detect_period(msg.subject)), opening_nav=opening, return_pct=ret, closing_nav=closing,
        management_fee=_to_float(got["mgmt"]), incentive_fee=_to_float(got["incentive"]),
        source_doc=path.name, received=msg.sent, extraction_method="fixed_width_text", confidence="medium",
        return_raw=got["return"] + " PCT", return_unit_inferred=unit,
        notes=" ".join(caveat.group(1).split()) if caveat else "",
        status_label=_status_label(msg), raw=got,
    )


# ------------------------------------------------------------ dispatch ---------
def _status_label(msg: Message, extra: str = "") -> str:
    text = (msg.subject + " " + msg.body + " " + extra).lower()
    labels = []
    for word in ("final", "estimate", "system generated", "audited internally", "confidential", "internal use"):
        if word in text:
            labels.append(word)
    return ", ".join(labels)


def parse_statements(messages: list[Message], attach_dir: Path = ATTACH_DIR) -> tuple[list[ManagerStatement], list[dict]]:
    """Parse every message classified as a manager statement.

    Returns (statements, failures). A failure is recorded, not raised, so one
    bad file never blocks the month."""
    statements: list[ManagerStatement] = []
    failures: list[dict] = []
    for msg in messages:
        if msg.category != "manager_statement":
            continue
        sleeve = MANAGER_DOMAINS.get(msg.sender_domain, msg.counterparty or "")
        try:
            if msg.attachment:
                path = attach_dir / msg.attachment
                if not path.exists():
                    raise FileNotFoundError(f"attachment {msg.attachment} referenced by {msg.filename} not in pack")
                if path.suffix == ".csv":
                    st = parse_csv_statement(path, msg)
                elif path.suffix in (".xlsx", ".xlsm"):
                    st = parse_xlsx_statement(path, msg, sleeve)
                elif path.suffix == ".txt":
                    st = parse_fixed_width_statement(path, msg, sleeve)
                else:
                    raise ValueError(f"unsupported attachment type {path.suffix}")
            else:
                st = parse_email_body_statement(msg, sleeve)
            if st.sleeve != sleeve and sleeve:
                st.notes = (st.notes + "; " if st.notes else "") + f"sleeve name in file: {st.sleeve!r}"
                st.sleeve = sleeve
            statements.append(st)
        except Exception as exc:  # noqa: BLE001 — a parse failure is data for a human, not a crash
            failures.append({"msg_id": msg.msg_id, "filename": msg.filename, "sleeve": sleeve, "error": str(exc)})
    return statements, failures
