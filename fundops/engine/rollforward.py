"""NAV roll-forward: sleeves → Master → vehicles → complex, driven by the effective treatments."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import MASTER_FUND, PERIOD, PRIOR_PERIOD
from ..models import CapitalInstruction, ManagerStatement
from .fees import incentive_fee_monthly, mgmt_fee_monthly


def sleeve_rollforward(prior: dict, fees: dict, statements: dict[str, ManagerStatement], treatments: dict[str, str]) -> pd.DataFrame:
    rows = []
    for _, r in prior["sleeves"].iterrows():
        name = r["sleeve"]
        aug_close = float(r["aug_close_nav_usd"])
        hwm = float(r["high_water_mark_usd"])
        st = statements.get(name)
        row = dict(sleeve=name, strategy=r["strategy"], prior_close=aug_close, hwm=hwm)
        if st is None:
            t = treatments.get(f"EXP-STATEMENT-{name}", "carry_flat")
            row.update(statement="MISSING", opening_reported=None, opening_break=None, net_return=None, closing_reported=None,
                       basis="carried flat at prior close" if t == "carry_flat" else "HOLD — no estimate",
                       closing_estimate=aug_close if t == "carry_flat" else None, pnl_vs_prior_close=0.0 if t == "carry_flat" else None,
                       unexplained=0.0, mgmt_fee_reported=None, mgmt_fee_recomputed=None, incentive_reported=None, incentive_recomputed=None,
                       above_hwm=None, source="")
        else:
            opening_break = st.opening_nav - aug_close
            t = treatments.get(f"STM-OPENTIE-{name}", "manager_closing")
            if abs(opening_break) <= 1.0:
                closing_est, basis, unexplained = st.closing_nav, "manager statement", 0.0
            elif t == "manager_closing":
                closing_est, basis, unexplained = st.closing_nav, "manager statement; opening break carried as unexplained", opening_break
            else:
                closing_est, basis, unexplained = aug_close * (1 + st.net_return), "manager return applied to our prior close", 0.0
            row.update(statement=st.fmt + ("" if st.formal else " (email only)"), opening_reported=st.opening_nav, opening_break=opening_break,
                       net_return=st.net_return, closing_reported=st.closing_nav, basis=basis, closing_estimate=closing_est,
                       pnl_vs_prior_close=closing_est - aug_close, unexplained=unexplained,
                       mgmt_fee_reported=st.mgmt_fee, mgmt_fee_recomputed=round(mgmt_fee_monthly(st.opening_nav, st.closing_nav, fees["mgmt_rate_pa"]), 0),
                       incentive_reported=st.incentive_fee, incentive_recomputed=round(incentive_fee_monthly(st.opening_nav, st.closing_nav, hwm, fees["incentive_rate"]), 0),
                       above_hwm=st.closing_nav > hwm, source=st.doc_id)
        rows.append(row)
    return pd.DataFrame(rows)


def master_rollforward(prior: dict, sleeves_rf: pd.DataFrame) -> dict:
    reported = sleeves_rf[sleeves_rf["statement"] != "MISSING"]
    carried = sleeves_rf[sleeves_rf["statement"] == "MISSING"]
    hold = sleeves_rf["closing_estimate"].isna().any()
    pnl_per_statements = float((reported["closing_reported"] - reported["opening_reported"]).sum())
    unexplained = float(sleeves_rf["unexplained"].sum())
    # when the treatment applies our opening × return, pnl_vs_prior_close already differs from the manager's P&L
    basis_adj = float(reported["pnl_vs_prior_close"].sum()) - pnl_per_statements - unexplained
    opening = prior["master_total"]
    closing = None if hold else float(sleeves_rf["closing_estimate"].sum()) + prior["master_cash"]
    lines = [
        dict(line=f"Opening NAV ({PRIOR_PERIOD} final, Lanyard)", usd=opening, note=prior["files"]["sleeves"]),
        dict(line="Net P&L per manager statements (7 sleeves)", usd=pnl_per_statements, note="closing − opening as each manager reported it"),
        dict(line="Unexplained opening-balance differences", usd=unexplained, note="manager opening ≠ our prior close; carried as a reconciling item"),
    ]
    if abs(basis_adj) > 0.5:
        lines.append(dict(line="Basis adjustment (return applied to our opening)", usd=basis_adj, note="treatment chosen on the exception"))
    lines += [
        dict(line=f"Sleeves carried flat (no statement): {', '.join(carried['sleeve'])}" if len(carried) else "Sleeves carried flat", usd=0.0,
             note=f"{', '.join(f'{s} {c:,.0f}' for s, c in zip(carried['sleeve'], carried['prior_close']))}" if len(carried) else ""),
        dict(line="Master cash & equivalents (carried; no September bank statement)", usd=0.0, note=f"held at {prior['master_cash']:,.0f}"),
        dict(line=f"Closing NAV estimate ({PERIOD})", usd=closing, note="HOLD" if hold else "sum of sleeve estimates + cash"),
    ]
    est_return = None if hold else (closing - opening) / opening
    return dict(opening=opening, closing=closing, est_return=est_return, lines=lines, hold=hold,
                carried_flat_usd=float(carried["prior_close"].sum()), unexplained=unexplained,
                sleeves_reported=int(len(reported)), sleeves_total=int(len(sleeves_rf)))


def vehicle_rollforward(prior: dict, fees: dict, master: dict, sleeves_rf: pd.DataFrame, statements: dict[str, ManagerStatement],
                        instructions: list[CapitalInstruction], capital_rows: list[dict], treatments: dict[str, str]) -> list[dict]:
    v = prior["vehicles"]
    lt = prior["lookthrough"]
    cap = {r["item_id"]: r for r in capital_rows}
    out = []
    for _, r in v.iterrows():
        name = r["vehicle"]
        frac = float(r["master_interest_usd"]) / prior["master_total"]
        mi_open = float(r["master_interest_usd"])
        mi_close = None if master["hold"] else frac * master["closing"]
        # direct investments
        direct_open = float(r["direct_investments_usd"])
        direct_close, direct_note = direct_open, ""
        for _, d in lt[(lt["vehicle"] == name) & lt["via"].str.lower().str.startswith("direct co")].iterrows():
            t = treatments.get(f"EXP-DIRECT-{name}", "proxy_sleeve_return")
            st = statements.get(d["manager"])
            if t == "proxy_sleeve_return" and st is not None:
                direct_close = float(d["exposure_usd"]) * (1 + st.net_return)
                direct_note = f"{d['manager']} co-investment proxied at the sleeve return {st.net_return:+.2%} (no valuation received)"
            else:
                direct_note = f"{d['manager']} co-investment carried flat (no valuation received)"
        cash_open = float(r["cash_usd"])
        subs_accepted = subs_pending = reds_effective = reds_pending = 0.0
        items = []
        for ins in instructions:
            if ins.vehicle != name:
                continue
            row = cap[ins.item_id]
            if ins.kind == "subscription":
                if row["status"].startswith("FUNDED – MATCHED"):
                    subs_accepted += ins.amount
                    items.append(f"{ins.investor} subscription {ins.amount:,.0f} dealt {ins.effective_date.isoformat()}")
                else:
                    t = treatments.get(f"CAP-SUB-{ins.item_id}", "hold_in_subscription_account")
                    if t == "accept_received_amount":
                        subs_pending += row["wire_usd"] or 0.0
                        items.append(f"{ins.investor}: {row['wire_usd']:,.0f} received, accepted for next dealing date (outside September NAV)")
                    else:
                        subs_pending += row["wire_usd"] or 0.0
                        items.append(f"{ins.investor}: {row['wire_usd'] or 0:,.0f} received against {ins.amount:,.0f} instructed — held in subscription account, outside NAV")
            else:
                t = treatments.get(f"CAP-RED-{ins.item_id}", "defer_per_terms")
                if row["status"].startswith("QUEUED – ELIGIBLE") or t == "process_as_queued":
                    reds_effective += ins.amount
                    items.append(f"{ins.investor} redemption {ins.amount:,.0f} effective {ins.effective_date.isoformat()}")
                else:
                    reds_pending += ins.amount
                    items.append(f"{ins.investor} redemption {ins.amount:,.0f} NOT effective 30 Sep (notice deficient) — pending GP decision; next eligible 31 Oct")
        admin = fees["admin_fee_per_vehicle_month"]
        pre = None if mi_close is None else mi_close + direct_close + cash_open + subs_accepted - admin
        post = None if pre is None else pre - reds_effective
        out.append(dict(
            vehicle=name, prior_nav=float(r["vehicle_nav_usd"]), ownership_fraction=frac,
            master_interest_open=mi_open, master_interest_close=mi_close,
            share_of_master_pnl=None if mi_close is None else mi_close - mi_open,
            direct_open=direct_open, direct_close=direct_close, direct_note=direct_note,
            cash_open=cash_open, subscriptions_accepted=subs_accepted, subscription_monies_held_outside_nav=subs_pending,
            admin_fee_accrual=-admin, nav_before_redemptions=pre, redemptions_effective=-reds_effective,
            redemptions_pending_decision=reds_pending, closing_nav_estimate=post,
            est_return_pre_flows=None if mi_close is None else ((mi_close - mi_open) + (direct_close - direct_open) - admin) / float(r["vehicle_nav_usd"]),
            capital_items=items,
        ))
    return out


def complex_summary(prior: dict, master: dict, vehicles: list[dict]) -> dict:
    opening = float(prior["vehicles"]["vehicle_nav_usd"].sum())
    hold = master["hold"]
    closing = None if hold else sum(x["closing_nav_estimate"] for x in vehicles)
    pre = None if hold else sum(x["nav_before_redemptions"] for x in vehicles)
    return dict(opening=opening, closing=closing, nav_before_redemptions=pre,
                subscriptions_accepted=sum(x["subscriptions_accepted"] for x in vehicles),
                subscription_monies_held=sum(x["subscription_monies_held_outside_nav"] for x in vehicles),
                redemptions_effective=sum(x["redemptions_effective"] for x in vehicles),
                redemptions_pending=sum(x["redemptions_pending_decision"] for x in vehicles),
                admin_fees=sum(x["admin_fee_accrual"] for x in vehicles), hold=hold)
