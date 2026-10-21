"""Capital activity: subscription / redemption instructions, transfer-agency
acknowledgements and bank credit advices → normalised records.

Instructions and wires are parsed separately and *matched* downstream (see
``checks/capital_checks.py``); nothing here assumes a wire belongs to an
instruction just because the names look alike.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from ..config import ATTACH_DIR
from ..models import CapitalInstruction, Message, WireCredit

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_long_date(s: str, default_year: int) -> date:
    """'15 September 2026', '12 Sep 2026', '15 September' → date."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?,?\s*(\d{4})?", s)
    if not m:
        raise ValueError(f"no date in {s!r}")
    mon = _MONTHS[m.group(2).lower()[:3]]
    year = int(m.group(3)) if m.group(3) else default_year
    return date(year, mon, int(m.group(1)))


def _amount(s: str) -> float:
    m = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", s)
    if not m:
        raise ValueError(f"no $ amount in {s!r}")
    return float(m.group(1).replace(",", ""))


def _vehicle_in(text: str, vehicles: list[str]) -> Optional[str]:
    t = " ".join(text.lower().split())
    for v in vehicles:
        if v.lower() in t:
            return v
    for v in vehicles:  # bank shorthand: "BRINDLEWOOD SELECT LP SUBSCRIPTION A/C"
        if v.lower().replace("brindlewood ", "") in t:
            return v
    return None


def _investor_in(text: str, investors: list[str]) -> Optional[str]:
    t = text.lower()
    for inv in investors:
        base = inv.lower().split(" (")[0]          # "Kestrel Point Advisors (clients)" → "kestrel point advisors"
        if base in t:
            return inv
    return None


def _parse_signed_form(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


def parse_capital_activity(messages: list[Message], vehicles: list[str], investors: list[str],
                           attach_dir: Path = ATTACH_DIR) -> tuple[list[CapitalInstruction], list[WireCredit], list[dict]]:
    instructions: list[CapitalInstruction] = []
    wires: list[WireCredit] = []
    failures: list[dict] = []

    for msg in messages:
        try:
            if msg.category == "subscription_instruction":
                text = msg.subject + "\n" + msg.body
                amount = _amount(msg.body)
                dealing = parse_long_date(re.search(r"for (?:the )?([^\n]*?dealing)", msg.body, re.I).group(1), msg.sent.year)
                signed = None
                notes = ""
                if msg.attachment:
                    form = _parse_signed_form(attach_dir / msg.attachment)
                    signed = msg.attachment
                    form_amt = _amount(form.get("amount", "$0"))
                    form_deal = parse_long_date(form.get("dealing date", ""), msg.sent.year) if form.get("dealing date") else None
                    notes = f"signed form: amount ${form_amt:,.0f}, dealing {form_deal}, {form.get('signed', '')}"
                    if form_amt != amount or (form_deal and form_deal != dealing):
                        notes += " — FORM DOES NOT MATCH EMAIL"
                else:
                    notes = "no signed subscription document attached — email instruction only"
                instructions.append(CapitalInstruction(
                    instr_id=f"SUB-{msg.msg_id}", kind="subscription",
                    investor=_investor_in(text, investors) or msg.counterparty or "?",
                    vehicle=_vehicle_in(text, vehicles) or "?", amount=amount,
                    dealing_date=dealing, notice_date=msg.sent, source_doc=msg.filename,
                    signed_document=signed, notes=notes))

            elif msg.category == "redemption_instruction":
                text = msg.subject + "\n" + msg.body
                amount = _amount(msg.body)
                eff = re.search(r"effective ([^,\n]+?)(?:,| per|\.)", msg.body, re.I)
                dealing = parse_long_date(eff.group(1), msg.sent.year)
                notice_m = re.search(r"(\d+)-day notice", msg.body, re.I)
                instructions.append(CapitalInstruction(
                    instr_id=f"RED-{msg.msg_id}", kind="redemption",
                    investor=_investor_in(text, investors) or msg.counterparty or "?",
                    vehicle=_vehicle_in(text, vehicles) or "?", amount=amount,
                    dealing_date=dealing, notice_date=msg.sent, source_doc=msg.filename,
                    signed_document=msg.attachment,
                    notes=f"instruction cites {notice_m.group(1)}-day notice provision" if notice_m else
                          "no signed redemption form attached — email instruction only"))

            elif msg.category == "ta_acknowledgement":
                amount = _amount(msg.body)
                vehicle = _vehicle_in(msg.body, vehicles)
                for ins in instructions:
                    if ins.kind == "redemption" and ins.amount == amount and ins.vehicle == vehicle:
                        ins.acknowledged_by_ta, ins.ta_ack_doc = True, msg.filename
                        break
                else:
                    failures.append({"msg_id": msg.msg_id, "filename": msg.filename,
                                     "error": "TA acknowledgement matches no instruction"})

            elif msg.category == "wire_credit":
                fields = {}
                for line in msg.body.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        fields[k.strip().lower()] = v.strip()
                wires.append(WireCredit(
                    wire_id=f"WIRE-{msg.msg_id}", account=fields.get("account", ""),
                    vehicle=_vehicle_in(fields.get("account", ""), vehicles) or "?",
                    amount=_amount(fields.get("amount", "")),
                    value_date=parse_long_date(fields.get("value date", ""), msg.sent.year),
                    originator=fields.get("originator", ""), reference=fields.get("reference", ""),
                    source_doc=msg.filename))
        except Exception as exc:  # noqa: BLE001
            failures.append({"msg_id": msg.msg_id, "filename": msg.filename, "error": str(exc)})

    return instructions, wires, failures
