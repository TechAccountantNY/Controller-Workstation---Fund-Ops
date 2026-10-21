"""The live 'what has arrived / what is outstanding' view.

The expected list is *derived* from the reference data (one statement per
sleeve, one valuation per direct position, one bank statement per cash
balance, one TA acknowledgement per instruction) rather than typed in, so a
new sleeve or a new instruction automatically creates an expectation."""
from __future__ import annotations

import pandas as pd

from .config import AS_OF, CAPITAL_CONFIRM_DUE, STATEMENTS_DUE
from .context import MonthContext


def build_arrivals(ctx: MonthContext) -> pd.DataFrame:
    rows = []
    for sleeve in ctx.sleeves:
        st = ctx.statement_for(sleeve)
        fail = next((f for f in ctx.statement_failures if f.get("sleeve") == sleeve), None)
        rows.append(dict(item=f"{ctx.period} statement — {sleeve}", kind="manager_statement", counterparty=sleeve,
                         expected_by=STATEMENTS_DUE, received_on=st.received if st else None,
                         document=st.source_doc if st else (fail["filename"] if fail else ""),
                         status=("RECEIVED" if st else ("RECEIVED — UNPARSEABLE" if fail else ("OUTSTANDING — OVERDUE" if AS_OF > STATEMENTS_DUE else "OUTSTANDING"))),
                         quality=(f"{st.extraction_method}, {st.confidence} confidence" + (f"; {st.status_label}" if st and st.status_label else "")) if st else ""))
    lt = ctx.ref.lookthrough
    for _, d in lt[lt.via.str.contains("co-investment", case=False)].iterrows():
        rows.append(dict(item=f"{ctx.period} valuation — {d.vehicle} direct co-investment in {d.manager}", kind="direct_valuation",
                         counterparty=d.manager, expected_by=STATEMENTS_DUE, received_on=None, document="",
                         status="OUTSTANDING — no document in pack", quality=""))
    rows.append(dict(item=f"{ctx.period} bank statement — master cash", kind="bank_statement", counterparty="Granite Harbor Bank, N.A.",
                     expected_by=STATEMENTS_DUE, received_on=None, document="",
                     status="AVAILABLE IN PORTAL — not retrieved", quality="notice 17 Sep"))
    for ins in ctx.instructions:
        rows.append(dict(item=f"TA acknowledgement — {ins.kind} {ins.investor} {ins.vehicle} ${ins.amount:,.0f}", kind="ta_acknowledgement",
                         counterparty="Lanyard Fund Services (Cayman) Ltd", expected_by=ins.dealing_date,
                         received_on=ctx.message(ins.ta_ack_doc.split('_')[0]).sent if ins.ta_ack_doc else None,
                         document=ins.ta_ack_doc or "", status="RECEIVED" if ins.acknowledged_by_ta else "OUTSTANDING", quality=""))
        if ins.kind == "subscription":
            wire = next((w for w in ctx.wires if w.vehicle == ins.vehicle and ins.investor.split()[0].lower() in w.originator.lower()), None)
            rows.append(dict(item=f"Funds — {ins.investor} → {ins.vehicle} ${ins.amount:,.0f}", kind="wire", counterparty=ins.investor,
                             expected_by=ins.dealing_date, received_on=wire.value_date if wire else None, document=wire.source_doc if wire else "",
                             status=("RECEIVED — MATCHES" if wire and abs(wire.amount - ins.amount) < 1 and wire.value_date <= ins.dealing_date
                                     else ("RECEIVED — DOES NOT MATCH" if wire else "OUTSTANDING")),
                             quality=f"${wire.amount:,.0f} value {wire.value_date:%d %b}" if wire else ""))
    rows.append(dict(item="Capital activity confirmation to Lanyard", kind="our_deliverable", counterparty="Lanyard Fund Services (Cayman) Ltd",
                     expected_by=CAPITAL_CONFIRM_DUE, received_on=None, document="", status="DUE TODAY — not sent", quality=""))
    for m in ctx.messages_in("investor_query"):
        if not m.subject.lower().startswith("re:"):
            rows.append(dict(item=f"Reply — {m.counterparty}: {m.subject}", kind="our_deliverable", counterparty=m.counterparty,
                             expected_by=m.sent, received_on=None, document=m.filename, status="OVERDUE — not sent", quality=""))
    df = pd.DataFrame(rows)
    return df
