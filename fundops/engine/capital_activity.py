"""Tie capital instructions to acknowledgements, wires, register terms and balances."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import AS_OF
from ..models import CapitalInstruction, WireCredit


def _investor_key(name: str) -> str:
    return name.lower().split(" (")[0].replace("family office", "").replace("trust", "").strip()


def match_wires(instructions: list[CapitalInstruction], wires: list[WireCredit]) -> dict[str, WireCredit]:
    """Match each subscription to at most one wire: same vehicle, originator mentions the investor."""
    matched: dict[str, WireCredit] = {}
    used: set[str] = set()
    for ins in instructions:
        if ins.kind != "subscription":
            continue
        key = _investor_key(ins.investor)
        for w in wires:
            if w.wire_id in used or w.vehicle != ins.vehicle:
                continue
            if key.split()[0] in w.originator.lower():
                matched[ins.item_id] = w
                used.add(w.wire_id)
                break
    return matched


def build_capital_activity(instructions: list[CapitalInstruction], wires: list[WireCredit],
                           register: pd.DataFrame, vehicles: pd.DataFrame,
                           treatments: dict[str, str]) -> list[dict]:
    matched = match_wires(instructions, wires)
    rows: list[dict] = []
    for ins in instructions:
        reg = register[register["investor"].str.lower() == ins.investor.lower()]
        reg = reg.iloc[0] if len(reg) else None
        row = dict(item_id=ins.item_id, type=ins.kind, investor=ins.investor, vehicle=ins.vehicle,
                   instructed_usd=ins.amount, effective_date=ins.effective_date.isoformat(),
                   instruction_doc=ins.doc_id, instruction_date=ins.notice_date.isoformat(),
                   signed_doc=ins.signed_form or "", ack_doc=ins.ack_doc_id or "",
                   prior_balance_usd=float(reg["aug_31_balance_usd"]) if reg is not None else None,
                   wire_doc="", wire_usd=None, wire_value_date="", wire_reference="", wire_vs_instruction_usd=None,
                   checks=[], status="", next_action="", treatment=treatments.get(ins.item_id, ""))
        if ins.kind == "subscription":
            w = matched.get(ins.item_id)
            if w:
                row.update(wire_doc=w.doc_id, wire_usd=w.amount, wire_value_date=w.value_date.isoformat(),
                           wire_reference=w.reference, wire_vs_instruction_usd=w.amount - ins.amount)
                if abs(w.amount - ins.amount) > 0.5:
                    row["checks"].append(f"AMOUNT: wire ${w.amount:,.0f} vs instruction ${ins.amount:,.0f} (short ${ins.amount - w.amount:,.0f})")
                else:
                    row["checks"].append("AMOUNT: wire matches instruction")
                if w.value_date > ins.effective_date:
                    row["checks"].append(f"TIMING: funds value-dated {w.value_date.isoformat()}, after the {ins.effective_date.isoformat()} dealing date")
                else:
                    row["checks"].append(f"TIMING: funds received {w.value_date.isoformat()}, before dealing date")
            else:
                row["checks"].append("FUNDS: no wire received")
            row["checks"].append("DOCS: signed subscription agreement on file" if ins.signed_form else "DOCS: no signed subscription agreement on file")
            row["checks"].append("TA: acknowledgement on file" if ins.ack_doc_id else "TA: no transfer-agency acknowledgement on file")
            funded_ok = w is not None and abs(w.amount - ins.amount) <= 0.5 and w.value_date <= ins.effective_date
            if funded_ok:
                row["status"] = "FUNDED – MATCHED – dealt " + ins.effective_date.isoformat()
                row["next_action"] = ("Confirm to Lanyard for the September capital activity; obtain signed subscription agreement"
                                      if not ins.signed_form else "Confirm to Lanyard for the September capital activity")
            elif w is None:
                row["status"] = "INSTRUCTED – NOT FUNDED"
                row["next_action"] = "Chase funds; do not deal"
            else:
                row["status"] = "RECEIVED – DOES NOT MATCH – HOLD"
                row["next_action"] = "Hold in subscription account; contact investor to resolve shortfall / re-date dealing"
        else:  # redemption
            notice_days = int(reg["notice_days"]) if reg is not None else None
            if notice_days is not None:
                earliest = ins.notice_date + dt.timedelta(days=notice_days)
                row["notice_required_days"] = notice_days
                row["earliest_eligible_date"] = earliest.isoformat()
                if earliest > ins.effective_date:
                    row["checks"].append(f"NOTICE: {notice_days} days required → earliest {earliest.isoformat()}; instruction asks for {ins.effective_date.isoformat()} ({(earliest - ins.effective_date).days} days short)")
                else:
                    row["checks"].append("NOTICE: satisfied")
                freq = str(reg["frequency"])
                row["checks"].append(f"FREQUENCY: register says {freq}; effective date is a month-end")
            if reg is not None:
                if ins.amount <= float(reg["aug_31_balance_usd"]):
                    row["checks"].append(f"BALANCE: ${ins.amount:,.0f} within prior balance ${float(reg['aug_31_balance_usd']):,.0f}")
                else:
                    row["checks"].append("BALANCE: exceeds prior balance")
            veh = vehicles[vehicles["vehicle"] == ins.vehicle]
            if len(veh):
                cash = float(veh["cash_usd"].iloc[0])
                if ins.amount > cash:
                    row["checks"].append(f"FUNDING: vehicle cash ${cash:,.0f} < redemption ${ins.amount:,.0f}; requires a Master redemption — not evidenced in the pack")
            row["checks"].append("TA: acknowledged and queued for the September dealing cycle" if ins.ack_doc_id else "TA: no acknowledgement on file")
            deficient = any(c.startswith("NOTICE:") and "short" in c for c in row["checks"])
            if deficient:
                row["status"] = "QUEUED BY TA – NOTICE DEFICIENT – DECISION REQUIRED"
                row["next_action"] = "GP to decide: waive notice (document it) or defer to next eligible dealing date; tell Lanyard either way before NAV is struck"
            else:
                row["status"] = "QUEUED – ELIGIBLE"
                row["next_action"] = "Confirm to Lanyard; fund from Master"
        row["days_open"] = (AS_OF - ins.notice_date).days
        rows.append(row)

    # Wires that matched nothing
    used = {w.wire_id for w in matched.values()}
    for w in wires:
        if w.wire_id not in used:
            rows.append(dict(item_id=w.wire_id, type="unmatched credit", investor=w.originator, vehicle=w.vehicle,
                             instructed_usd=None, effective_date="", instruction_doc="", instruction_date="",
                             signed_doc="", ack_doc="", prior_balance_usd=None, wire_doc=w.doc_id, wire_usd=w.amount,
                             wire_value_date=w.value_date.isoformat(), wire_reference=w.reference,
                             wire_vs_instruction_usd=None, checks=["no instruction matches this credit"],
                             status="UNMATCHED CREDIT", next_action="Identify and return or apply", treatment="",
                             days_open=(AS_OF - w.value_date).days))
    return rows
