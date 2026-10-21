"""What should have arrived by now, and did it?

Expectations are generated from the prior close, not hand-typed: every sleeve
with a balance expects a statement for the period; every direct investment
expects a valuation; every pool of cash expects a bank statement.
"""
from __future__ import annotations

from ..config import AS_OF, MASTER_FUND, PERIOD
from ..models import Email, ManagerStatement


def build_expectations(prior: dict, statements: dict[str, ManagerStatement], emails: list[Email]) -> list[dict]:
    rows: list[dict] = []
    bank_notice = next((e for e in emails if e.kind == "bank_statement_notice"), None)

    for _, s in prior["sleeves"].iterrows():
        name = s["sleeve"]
        st = statements.get(name)
        if st is None:
            rows.append(dict(expected=f"{PERIOD} statement", counterparty=name, scope=MASTER_FUND,
                             basis=f"sleeve carried ${s['aug_close_nav_usd']:,.0f} at {prior['files']['sleeves']}",
                             status="MISSING", received=None, doc_id=None, fmt=None, formal=None,
                             note=f"nothing received for {PERIOD} as of {AS_OF.isoformat()}"))
        else:
            status = "RECEIVED" if st.formal else "RECEIVED (email figures only)"
            rows.append(dict(expected=f"{PERIOD} statement", counterparty=name, scope=MASTER_FUND,
                             basis=f"sleeve carried ${s['aug_close_nav_usd']:,.0f} at {prior['files']['sleeves']}",
                             status=status, received=st.received.isoformat(), doc_id=st.doc_id, fmt=st.fmt,
                             formal=st.formal, note=st.notes))

    for _, lt in prior["lookthrough"].iterrows():
        if str(lt["via"]).lower().startswith("direct co"):
            rows.append(dict(expected=f"{PERIOD} valuation of direct co-investment", counterparty=lt["manager"],
                             scope=lt["vehicle"], basis=f"${lt['exposure_usd']:,.0f} at {prior['files']['lookthrough']}",
                             status="MISSING", received=None, doc_id=None, fmt=None, formal=None,
                             note="manager statement received covers the Master sleeve only; no separate valuation for the co-investment"))

    cash_scopes = [(MASTER_FUND, prior["master_cash"])] + [
        (v["vehicle"], float(v["cash_usd"])) for _, v in prior["vehicles"].iterrows()]
    for scope, bal in cash_scopes:
        rows.append(dict(expected=f"{PERIOD} bank / cash statement", counterparty="Granite Harbor Bank, N.A.",
                         scope=scope, basis=f"cash carried ${bal:,.0f} in prior close",
                         status="MISSING (portal notice only)" if bank_notice else "MISSING",
                         received=bank_notice.date.isoformat() if bank_notice else None,
                         doc_id=bank_notice.doc_id if bank_notice else None, fmt=None, formal=None,
                         note="bank says the statement is available in the portal; it is not in the pack"))
    return rows
