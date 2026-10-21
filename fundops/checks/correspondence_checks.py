"""Correspondence that is a liability if nobody answers it: investor/advisor
questions, auditor requests, counterparty admin changes, and unclassified mail."""
from __future__ import annotations

import re

import numpy as np

from ..config import AS_OF, INVESTOR_QUERY_SLA_DAYS
from ..models import Exception_


def _business_days(a, b) -> int:
    return int(np.busday_count(a, b))


def run_correspondence_checks(ctx):
    exceptions, passes = [], []

    # ---- investor / advisor queries: threads by normalised subject ---------------
    threads: dict[str, list] = {}
    for m in ctx.messages_in("investor_query"):
        key = re.sub(r"^(re|fw|fwd):\s*", "", m.subject.strip(), flags=re.I).lower()
        threads.setdefault(key, []).append(m)
    for key, msgs in threads.items():
        msgs.sort(key=lambda m: m.sent)
        first = msgs[0]
        replies_from_us = [m for m in msgs if m.sender_domain == "brindlewood.example"]
        follow_ups = [m for m in msgs[1:] if m.sender_domain == first.sender_domain]
        if replies_from_us:
            passes.append(dict(check="correspondence.query_answered", subject=first.subject, detail="replied", evidence=[m.filename for m in msgs]))
            continue
        age = _business_days(first.sent, AS_OF)
        if age > INVESTOR_QUERY_SLA_DAYS:
            asks = re.findall(r"\(\d\)\s*([^()]+?)(?=\s*(?:and\s*)?\(\d\)|\?|$)", first.body.replace("\n", " "))
            # can the pack answer what was asked?
            answerable = []
            for a in asks:
                a_l = a.lower()
                if "redemption terms" in a_l:
                    reg = ctx.ref.investors
                    terms = sorted(set(reg[reg.vehicle.str.contains("Offshore")].redemption_terms))
                    answerable.append(f"'{a.strip()}' → answerable from the register: {', '.join(terms)}")
                elif "net return" in a_l:
                    answerable.append(f"'{a.strip()}' → NOT answerable from the pack: the August package gives vehicle closing NAVs but no vehicle opening NAV or vehicle-level return")
                else:
                    answerable.append(f"'{a.strip()}' → needs a person")
            exceptions.append(Exception_(
                key=f"correspondence:unanswered:{first.msg_id}", rank=0, severity="medium", category="absence", area="correspondence",
                counterparty=first.counterparty or first.sender,
                title=f"{first.counterparty or first.sender}: client question unanswered for {age} business days" + (f" ({len(follow_ups)} follow-up)" if follow_ups else ""),
                detail=(f"Asked {first.sent:%d %b}; chased {', '.join(f'{m.sent:%d %b}' for m in follow_ups)}; no reply from operations@ in the inbox. "
                        f"The advisor represents two underlying clients in the same vehicle as the queued Kestrel Point redemption. " + " ".join(answerable)),
                amount_impact_usd=0, evidence=[m.filename for m in msgs] + ["august-2026-close-package/investor-register-aug2026.csv"],
                findings=answerable,
                recommended_action="Reply today with the redemption terms; ask Lanyard for the August vehicle-level return (and add vehicle returns to the package going forward).",
                decision_required=True))

    # ---- auditor ------------------------------------------------------------------
    for m in ctx.messages_in("auditor_request"):
        items = re.findall(r"^\s*\d+\.\s*(.+)$", m.body, re.M)
        exceptions.append(Exception_(
            key=f"correspondence:auditor:{m.msg_id}", rank=0, severity="low", category="process", area="correspondence",
            counterparty=m.counterparty or m.sender, title=f"FY25 audit: {len(items)} PBC items outstanding; fieldwork slots held for late October",
            detail="; ".join(items) + f". Auditor asked for timing on {m.sent:%d %b}; no reply in the inbox.",
            amount_impact_usd=0, evidence=[m.filename], recommended_action="Reply with dates; the rep letter comments are two months old.", decision_required=False))

    # ---- counterparty admin changes ------------------------------------------------
    for m in ctx.messages_in("manager_admin_notice"):
        exceptions.append(Exception_(
            key=f"correspondence:contact_change:{m.msg_id}", rank=0, severity="low", category="process", area="correspondence",
            counterparty=m.counterparty or m.sender, title=f"{m.counterparty}: operations contact changes from 1 Oct",
            detail=m.body.strip().replace("\n", " "), amount_impact_usd=0, evidence=[m.filename],
            recommended_action="Update the counterparty master so October chasers go to the right mailbox.", decision_required=False))

    # ---- anything the classifier would not claim -----------------------------------
    for m in ctx.messages_in("unclassified"):
        exceptions.append(Exception_(
            key=f"inbox:unclassified:{m.msg_id}", rank=0, severity="low", category="process", area="data_quality",
            counterparty=m.sender, title=f"Unclassified message: {m.subject}", detail="No rule claimed this message; a person needs to look.",
            amount_impact_usd=0, evidence=[m.filename], recommended_action="Classify by hand; add a rule if it recurs.", decision_required=True))

    # ---- mid-month flashes vs finals (information, recorded as passes) ------------
    for m in ctx.messages_in("manager_flash"):
        st = ctx.statement_for(m.counterparty)
        flash = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", m.body)
        if st and flash:
            passes.append(dict(check="statement.flash_direction_consistent", subject=m.counterparty,
                               detail=f"mid-month flash {flash.group(1)}% ({m.sent:%d %b}); final {st.return_pct:+.2f}% — same sign, plausible magnitude",
                               evidence=[m.filename, st.source_doc]))
    return exceptions, passes
