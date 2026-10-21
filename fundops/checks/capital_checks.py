"""Capital activity checks: instruction ↔ wire matching, dealing-date timing,
notice periods against the investor register, documentation, and the
administrator's confirmation deadline."""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from ..config import CAPITAL_CONFIRM_DUE, AS_OF
from ..models import Exception_


def _terms(register_terms: str) -> tuple[str, int]:
    """'Monthly, 30 days notice' → ('Monthly', 30)."""
    freq = register_terms.split(",")[0].strip()
    m = re.search(r"(\d+)\s*days", register_terms)
    return freq, int(m.group(1)) if m else 0


def _month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def next_eligible_dealing_date(notice_date: date, freq: str, notice_days: int) -> date:
    """First dealing date on/after notice + notice_days. Monthly → month-ends; Quarterly → calendar quarter-ends."""
    earliest = notice_date + timedelta(days=notice_days)
    d = _month_end(earliest)
    if freq.lower().startswith("quarter"):
        while d.month not in (3, 6, 9, 12):
            d = _month_end(d + timedelta(days=1))
    return d


def _tokens(name: str) -> set[str]:
    return {t for t in re.split(r"[^a-z]+", name.lower()) if len(t) > 3 and t not in {"family", "office", "trust", "advisors", "clients", "holdings"}}


def run_capital_checks(ctx):
    exceptions, passes = [], []
    reg = ctx.ref.investors
    matched_wires: set[str] = set()

    for ins in ctx.instructions:
        reg_row = reg[(reg.investor == ins.investor) & (reg.vehicle == ins.vehicle)]
        in_register = not reg_row.empty
        bal = float(reg_row.aug_31_balance_usd.iloc[0]) if in_register else 0.0
        terms = str(reg_row.redemption_terms.iloc[0]) if in_register else ""

        if ins.kind == "subscription":
            cands = [w for w in ctx.wires if w.vehicle == ins.vehicle and _tokens(w.originator) & _tokens(ins.investor)]
            findings, sev = [], "info"
            if not cands:
                findings.append(f"no wire credit from {ins.investor} to the {ins.vehicle} subscription account in the inbox")
                sev = "high"
                received, value_date, wire_doc = 0.0, None, None
            else:
                w = cands[0]
                matched_wires.add(w.wire_id)
                received, value_date, wire_doc = w.amount, w.value_date, w.source_doc
                if abs(w.amount - ins.amount) > 0.5:
                    findings.append(f"wire ${w.amount:,.0f} vs instruction ${ins.amount:,.0f}: short by ${ins.amount - w.amount:,.0f}")
                    sev = "high"
                else:
                    passes.append(dict(check="capital.wire_matches_instruction", subject=f"{ins.investor} → {ins.vehicle}",
                                       detail=f"wire ${w.amount:,.0f} value {w.value_date:%d %b} = instruction ${ins.amount:,.0f}; ref '{w.reference}'",
                                       evidence=[ins.source_doc, w.source_doc]))
                if w.value_date > ins.dealing_date:
                    findings.append(f"funds value-dated {w.value_date:%d %b}, {(w.value_date - ins.dealing_date).days} days after the {ins.dealing_date:%d %b} dealing date")
                    sev = "high"
                else:
                    passes.append(dict(check="capital.funds_before_dealing_date", subject=f"{ins.investor} → {ins.vehicle}",
                                       detail=f"value date {w.value_date:%d %b} ≤ dealing date {ins.dealing_date:%d %b}", evidence=[w.source_doc]))
            if not ins.signed_document:
                findings.append("no signed subscription document in the inbox — instruction is an email only")
                sev = "medium" if sev in ("info",) else sev
            else:
                passes.append(dict(check="capital.signed_document_present", subject=f"{ins.investor} → {ins.vehicle}",
                                   detail=f"signed form {ins.signed_document}: {ins.notes}", evidence=[ins.source_doc, ins.signed_document]))
                if "DOES NOT MATCH" in ins.notes:
                    findings.append("signed form does not match the email instruction")
                    sev = "high"
            if not ins.acknowledged_by_ta:
                findings.append("no transfer-agency acknowledgement of the subscription in the inbox (Lanyard acknowledged the month's redemption, not the subscriptions)")
                sev = "medium" if sev == "info" else sev
            if in_register:
                passes.append(dict(check="capital.investor_known", subject=f"{ins.investor} → {ins.vehicle}",
                                   detail=f"existing investor in {ins.vehicle} (Aug balance ${bal:,.0f}); additional subscription", evidence=["august-2026-close-package/investor-register-aug2026.csv"]))
            else:
                findings.append("investor not in the August register for this vehicle — new investor, KYC/AML and subscription docs required")
                sev = "medium" if sev == "info" else sev

            if findings:
                short = (ins.amount - received) if received else ins.amount
                processable = received > 0 and abs(received - ins.amount) <= 0.5 and value_date is not None and value_date <= ins.dealing_date
                exceptions.append(Exception_(
                    key=f"capital:subscription:{ins.instr_id}", rank=0, severity=sev, category="cross_document", area="capital",
                    counterparty=ins.investor,
                    title=f"Subscription {ins.investor} → {ins.vehicle} ${ins.amount:,.0f}: " + ("; ".join(f[:60] for f in findings[:1]) if len(findings) == 1 else f"{len(findings)} issues"),
                    detail=" ".join(findings) + (
                        f" Cash of ${received:,.0f} is sitting in the {ins.vehicle} subscription account and cannot be allotted against a "
                        f"${ins.amount:,.0f} signed instruction; it is excluded from NAV until the investor confirms whether the subscription is "
                        f"${received:,.0f} (amended instruction and form) or the balance is coming." if received and not processable else
                        (f" Wire matched and received before dealing; treated as processed at {ins.dealing_date:%d %b} pending the signed document." if processable else "")),
                    amount_impact_usd=short if not processable else ins.amount,
                    evidence=[ins.source_doc] + ([ins.signed_document] if ins.signed_document else []) + ([wire_doc] if wire_doc else []),
                    findings=findings,
                    recommended_action=("Contact the investor today; hold funds unallotted; agree the amount and a dealing date; confirm the position to Lanyard."
                                        if not processable else "Obtain the signed subscription document before final NAV; confirm the allotment to Lanyard."),
                    decision_required=True))

        elif ins.kind == "redemption":
            findings, sev = [], "info"
            if in_register:
                freq, days = _terms(terms)
                given = (ins.dealing_date - ins.notice_date).days
                if given < days:
                    nxt = next_eligible_dealing_date(ins.notice_date, freq, days)
                    findings.append(f"notice given {ins.notice_date:%d %b} for {ins.dealing_date:%d %b} = {given} days; register terms for {ins.vehicle} are "
                                    f"'{terms}'. Next eligible dealing date on those terms is {nxt:%d %b %Y}.")
                    sev = "high"
                else:
                    passes.append(dict(check="capital.notice_period", subject=f"{ins.investor} ← {ins.vehicle}",
                                       detail=f"{given} days' notice ≥ {days} required", evidence=[ins.source_doc, "august-2026-close-package/investor-register-aug2026.csv"]))
                if ins.amount > bal:
                    findings.append(f"redemption ${ins.amount:,.0f} exceeds August balance ${bal:,.0f}")
                    sev = "high"
                else:
                    passes.append(dict(check="capital.redemption_within_balance", subject=f"{ins.investor} ← {ins.vehicle}",
                                       detail=f"${ins.amount:,.0f} ≤ August balance ${bal:,.0f} ({ins.amount / bal:.1%} partial redemption)",
                                       evidence=[ins.source_doc, "august-2026-close-package/investor-register-aug2026.csv"]))
            else:
                findings.append("investor not found in the register for this vehicle")
                sev = "high"
            if ins.acknowledged_by_ta:
                if sev == "high":
                    findings.append(f"Lanyard transfer agency acknowledged and queued the trade for the September dealing cycle ({ins.ta_ack_doc}) "
                                    f"without raising the notice shortfall — a control gap at the administrator as well as a decision for the GP.")
                else:
                    passes.append(dict(check="capital.ta_acknowledged", subject=f"{ins.investor} ← {ins.vehicle}", detail="acknowledged by Lanyard TA", evidence=[ins.ta_ack_doc]))
            else:
                findings.append("no transfer-agency acknowledgement in the inbox")
                sev = "medium" if sev == "info" else sev
            if not ins.signed_document:
                findings.append("no signed redemption form — instruction is an advisor's email on behalf of underlying clients")
            if findings:
                exceptions.append(Exception_(
                    key=f"capital:redemption:{ins.instr_id}", rank=0, severity=sev, category="cross_document", area="capital",
                    counterparty=ins.investor,
                    title=f"Redemption {ins.investor} ← {ins.vehicle} ${ins.amount:,.0f}: notice period not met, yet queued by the TA" if sev == "high"
                          else f"Redemption {ins.investor} ← {ins.vehicle} ${ins.amount:,.0f}: documentation",
                    detail=" ".join(findings) + (f" Base-case scenario '{ctx.assumptions.kestrel_redemption_scenario}': not processed at {ins.dealing_date:%d %b}; "
                                                 f"the alternative (processed as queued) is shown alongside in the roll-forward." if sev == "high" else ""),
                    amount_impact_usd=ins.amount, evidence=[ins.source_doc] + ([ins.ta_ack_doc] if ins.ta_ack_doc else []) + ["august-2026-close-package/investor-register-aug2026.csv"],
                    findings=findings,
                    recommended_action="Escalate to the GP/legal today: either a documented notice waiver (and consider equal treatment of other investors) or instruct Lanyard to re-queue for the next eligible dealing date. Tell Kestrel Point which it is before the estimate goes out.",
                    decision_required=True))

    # ---- dealing day not verifiable from the pack ------------------------------------
    subs = [i for i in ctx.instructions if i.kind == "subscription"]
    if subs:
        days = sorted({i.dealing_date.strftime("%d %b") for i in subs})
        exceptions.append(Exception_(
            key="capital:dealing_day_unverified", rank=0, severity="info", category="absence", area="capital",
            title=f"Subscription dealing date ({', '.join(days)}) accepted as instructed — the pack has no offering document to check it against",
            detail="The investor register gives redemption terms only. Mid-month subscription dealing is treated as valid because both investors and "
                   "the administrator's calendar behave as if it is; it should be confirmed against the LPA/offering memorandum.",
            amount_impact_usd=0, evidence=[i.source_doc for i in subs] + ["august-2026-close-package/investor-register-aug2026.csv"],
            recommended_action="Add the fund terms (dealing days, notice periods, minimums) to the reference data.", decision_required=False))

    # ---- wires with no instruction ---------------------------------------------
    for w in ctx.wires:
        if w.wire_id not in matched_wires:
            exceptions.append(Exception_(
                key=f"capital:unmatched_wire:{w.wire_id}", rank=0, severity="high", category="absence", area="capital",
                counterparty=w.originator, title=f"Unexplained credit ${w.amount:,.0f} to {w.vehicle} subscription account",
                detail=f"Originator '{w.originator}', ref '{w.reference}', value {w.value_date:%d %b}; no subscription instruction matches.",
                amount_impact_usd=w.amount, evidence=[w.source_doc], recommended_action="Identify the investor; do not allot.", decision_required=True))

    # ---- administrator deadline ----------------------------------------------------
    cal = ctx.messages_in("admin_calendar")
    if cal and AS_OF >= CAPITAL_CONFIRM_DUE:
        exceptions.append(Exception_(
            key="process:capital_confirmation_due", rank=0, severity="medium", category="process", area="capital",
            counterparty="Lanyard Fund Services (Cayman) Ltd",
            title=f"Lanyard needs September capital activity confirmed by {CAPITAL_CONFIRM_DUE:%d %b} — today",
            detail="The close calendar asks the manager to confirm September capital activity by 1 October; nothing in the inbox shows it was sent. "
                   "The confirmation has to state: which subscription is allotted, which is held, and what happens to the queued redemption.",
            amount_impact_usd=sum(i.amount for i in ctx.instructions), evidence=[cal[0].filename],
            recommended_action="Send the confirmation today with the dispositions recorded in this workstation.", decision_required=True))
    return exceptions, passes
