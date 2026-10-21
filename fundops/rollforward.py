"""NAV roll-forward: sleeve → master → vehicle → complex, plus the capital
activity state. Every row says where its number came from and whether it is a
statement figure, a policy (assumption), or an arithmetic consequence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .config import Assumptions
from .context import MonthContext
from .fees import implied_closing
from .ingest.reference import prior_close_nav


@dataclass
class RollForward:
    sleeves: pd.DataFrame
    master: pd.DataFrame
    vehicles: pd.DataFrame
    complex_: pd.DataFrame
    capital: pd.DataFrame
    scenario_vehicles: pd.DataFrame     # same as vehicles under the alternative redemption scenario
    assumptions: list[dict] = field(default_factory=list)


def sleeve_rollforward(ctx: MonthContext) -> pd.DataFrame:
    rows = []
    for sleeve in ctx.sleeves:
        prior = prior_close_nav(ctx.ref, sleeve)
        st = ctx.statement_for(sleeve)
        if st is None:
            rows.append(dict(sleeve=sleeve, aug_close_nav=prior, sep_opening_per_manager=None, opening_tie_diff=None,
                             net_return_pct=0.0, sep_closing_per_manager=None, estimate_nav=prior,
                             estimate_basis=f"POLICY {ctx.assumptions.missing_statement_policy}: no statement — carried at August close, 0.00%",
                             estimate_source="assumption", alt_nav=None, range_usd=None,
                             mgmt_fee_reported=None, incentive_fee_reported=None, source_doc="(none)", confidence="none"))
            continue
        tie = st.opening_nav - prior
        alt = None
        basis = f"manager closing NAV ({st.extraction_method})"
        est = st.closing_nav
        src = "statement"
        if abs(tie) > 1:
            alt = implied_closing(prior, st.return_pct)
            if ctx.assumptions.stonebow_policy == "reported_closing":
                basis = (f"POLICY {ctx.assumptions.stonebow_policy}: manager closing used (lower); opening break Δ {tie:,.0f} unresolved; "
                         f"alt = Aug close × (1+{st.return_pct:.2f}%) = {alt:,.0f}")
                src = "statement (disputed)"
            else:
                est, basis, src = alt, "Aug close × manager return (opening break)", "derived"
        rows.append(dict(sleeve=sleeve, aug_close_nav=prior, sep_opening_per_manager=st.opening_nav, opening_tie_diff=tie,
                         net_return_pct=st.return_pct, sep_closing_per_manager=st.closing_nav, estimate_nav=est,
                         estimate_basis=basis, estimate_source=src, alt_nav=alt,
                         range_usd=(alt - est) if alt is not None else None,
                         mgmt_fee_reported=st.management_fee, incentive_fee_reported=st.incentive_fee,
                         source_doc=st.source_doc, confidence=st.confidence))
    df = pd.DataFrame(rows)
    df["pnl_estimate"] = df["estimate_nav"] - df["aug_close_nav"]
    return df


def master_rollforward(ctx: MonthContext, sleeves: pd.DataFrame) -> pd.DataFrame:
    cash = ctx.ref.master_cash_prior
    inv_aug = float(sleeves.aug_close_nav.sum())
    inv_sep = float(sleeves.estimate_nav.sum())
    rows = [
        dict(line="Investments in sleeves — August close", amount=inv_aug, basis="Σ sleeves, Lanyard August package"),
        dict(line="Master cash & equivalents — August close", amount=cash, basis="Lanyard August package"),
        dict(line="Master NAV — August close", amount=inv_aug + cash, basis="ties to package total" if abs(inv_aug + cash - ctx.ref.master_total_prior) < 1 else "DOES NOT TIE"),
        dict(line="Sleeve P&L — September (estimate)", amount=inv_sep - inv_aug, basis="Σ sleeve estimate NAV − August close (see sleeve table for basis per sleeve)"),
        dict(line="Master cash movement — September", amount=0.0, basis=f"POLICY {ctx.assumptions.master_cash_policy}: no bank statement in pack"),
        dict(line="Capital flows at master level — September", amount=0.0, basis=f"POLICY {ctx.assumptions.subscription_cash_policy}: subscriptions held in vehicle cash; redemption scenario applied at vehicle level"),
        dict(line="Master NAV — September estimate", amount=inv_sep + cash, basis="arithmetic"),
        dict(line="  of which on final/received statements", amount=float(sleeves.loc[sleeves.estimate_source == "statement", "estimate_nav"].sum()) + cash,
             basis="sleeves with a statement that ties internally and to prior close, plus cash"),
        dict(line="  of which disputed or assumed", amount=float(sleeves.loc[sleeves.estimate_source != "statement", "estimate_nav"].sum()),
             basis="; ".join(f"{r.sleeve}: {r.estimate_source}" for r in sleeves.itertuples() if r.estimate_source != "statement")),
        dict(line="Upside if disputed items resolve as alternatives", amount=float(sleeves.range_usd.fillna(0).sum()), basis="Σ (alt − estimate) where an alternative exists"),
    ]
    return pd.DataFrame(rows)


def capital_state(ctx: MonthContext, scenario: str) -> pd.DataFrame:
    """One row per instruction and per wire, with the status the policies imply."""
    rows = []
    for ins in ctx.instructions:
        wire = next((w for w in ctx.wires if w.vehicle == ins.vehicle and any(t in w.originator.lower() for t in ins.investor.lower().split()[:1])), None)
        if ins.kind == "subscription":
            received = wire.amount if wire else 0.0
            ok_amt = wire is not None and abs(wire.amount - ins.amount) <= 0.5
            ok_date = wire is not None and wire.value_date <= ins.dealing_date
            if ok_amt and ok_date:
                status, nav_effect, where = "PROCESSED (pending signed doc)" if not ins.signed_document else "PROCESSED", ins.amount, f"{ins.vehicle} cash"
            else:
                status, nav_effect, where = "HELD — not allotted", 0.0, f"{ins.vehicle} subscription a/c (excluded from NAV)"
            rows.append(dict(item=ins.instr_id, kind="subscription", investor=ins.investor, vehicle=ins.vehicle,
                             instructed=ins.amount, received=received, difference=received - ins.amount,
                             dealing_date=ins.dealing_date, funds_value_date=wire.value_date if wire else None,
                             signed_document=ins.signed_document or "", ta_acknowledged="", status=status,
                             nav_effect=nav_effect, cash_location=where, source_docs=" | ".join(filter(None, [ins.source_doc, ins.signed_document, wire.source_doc if wire else None]))))
        else:
            processed = scenario == "as_queued"
            rows.append(dict(item=ins.instr_id, kind="redemption", investor=ins.investor, vehicle=ins.vehicle,
                             instructed=ins.amount, received=None, difference=None, dealing_date=ins.dealing_date, funds_value_date=None,
                             signed_document=ins.signed_document or "", ta_acknowledged="yes" if ins.acknowledged_by_ta else "no",
                             status=("PROCESSED at 30 Sep as queued by TA (scenario)" if processed else "HELD — notice period not met; next eligible 31 Oct 2026 (base case)"),
                             nav_effect=-ins.amount if processed else 0.0,
                             cash_location="redemption payable" if processed else "n/a",
                             source_docs=" | ".join(filter(None, [ins.source_doc, ins.ta_ack_doc]))))
    return pd.DataFrame(rows)


def vehicle_rollforward(ctx: MonthContext, sleeves: pd.DataFrame, capital: pd.DataFrame) -> pd.DataFrame:
    veh = ctx.ref.vehicles
    lt = ctx.ref.lookthrough
    inv_aug = float(sleeves.aug_close_nav.sum())
    master_pnl = float(sleeves.estimate_nav.sum()) - inv_aug
    admin = ctx.ref.fees.admin_fee_per_vehicle_month if ctx.assumptions.admin_fee_accrual else 0.0
    rows = []
    for _, v in veh.iterrows():
        own = float(v.master_ownership_pct) / 100.0
        pnl_share = master_pnl * own
        # direct co-investments rolled at the manager's sleeve return (policy)
        direct_aug = float(v.direct_investments_usd)
        direct_pnl = 0.0
        direct_basis = ""
        for _, d in lt[(lt.vehicle == v.vehicle) & lt.via.str.contains("co-investment", case=False)].iterrows():
            r = sleeves.loc[sleeves.sleeve == d.manager, "net_return_pct"].iloc[0]
            direct_pnl += float(d.exposure_usd) * r / 100.0
            direct_basis = f"POLICY {ctx.assumptions.direct_coinvest_policy}: {d.manager} sleeve return {r:.2f}% applied to ${d.exposure_usd:,.0f}"
        subs = float(capital[(capital.vehicle == v.vehicle) & (capital.kind == "subscription")].nav_effect.sum())
        reds = float(capital[(capital.vehicle == v.vehicle) & (capital.kind == "redemption")].nav_effect.sum())
        held = float(capital[(capital.vehicle == v.vehicle) & (capital.status.str.startswith("HELD")) & (capital.kind == "subscription")].received.sum())
        sep_nav = float(v.vehicle_nav_usd) + pnl_share + direct_pnl + subs + reds - admin
        rows.append(dict(
            vehicle=v.vehicle, aug_nav=float(v.vehicle_nav_usd), master_ownership_pct_aug=float(v.master_ownership_pct),
            master_pnl_share=pnl_share, direct_coinvest_pnl=direct_pnl, subscriptions=subs, redemptions=reds,
            admin_fee_accrual=-admin, sep_nav_estimate=sep_nav, sep_return_pct=(pnl_share + direct_pnl - admin) / float(v.vehicle_nav_usd) * 100.0,
            master_interest_sep=float(v.master_interest_usd) + pnl_share, direct_investments_sep=direct_aug + direct_pnl,
            cash_sep=float(v.cash_usd) + subs + reds - admin,
            cash_held_unallotted=held, direct_basis=direct_basis,
            basis=(f"Aug NAV + {v.master_ownership_pct}% × master P&L {master_pnl:,.0f} (POLICY {ctx.assumptions.pnl_allocation_basis}) "
                   f"+ direct P&L + processed subscriptions − processed redemptions − admin fee {admin:,.0f}")))
    df = pd.DataFrame(rows)
    return df


def complex_rollforward(ctx: MonthContext, vehicles: pd.DataFrame, sleeves: pd.DataFrame) -> pd.DataFrame:
    aug = float(vehicles.aug_nav.sum())
    rows = [
        dict(line="Complex NAV — August close (Σ vehicles)", amount=aug),
        dict(line="Master P&L allocated to vehicles", amount=float(vehicles.master_pnl_share.sum())),
        dict(line="Direct co-investment P&L (proxy)", amount=float(vehicles.direct_coinvest_pnl.sum())),
        dict(line="Subscriptions processed", amount=float(vehicles.subscriptions.sum())),
        dict(line="Redemptions processed", amount=float(vehicles.redemptions.sum())),
        dict(line="Administration fee accrual", amount=float(vehicles.admin_fee_accrual.sum())),
        dict(line="Complex NAV — September estimate (Σ vehicles)", amount=float(vehicles.sep_nav_estimate.sum())),
        dict(line="Cash held unallotted (excluded from NAV)", amount=float(vehicles.cash_held_unallotted.sum())),
        dict(line="Range: upside if disputed sleeve items resolve", amount=float(sleeves.range_usd.fillna(0).sum())),
        dict(line="Share of master NAV on clean statements", amount=float(sleeves.loc[sleeves.estimate_source == "statement", "estimate_nav"].sum() + ctx.ref.master_cash_prior) / float(sleeves.estimate_nav.sum() + ctx.ref.master_cash_prior) * 100.0),
    ]
    return pd.DataFrame(rows)


def build(ctx: MonthContext) -> RollForward:
    sleeves = sleeve_rollforward(ctx)
    master = master_rollforward(ctx, sleeves)
    base_cap = capital_state(ctx, ctx.assumptions.kestrel_redemption_scenario)
    alt_scn = "as_queued" if ctx.assumptions.kestrel_redemption_scenario == "deferred" else "deferred"
    alt_cap = capital_state(ctx, alt_scn)
    vehicles = vehicle_rollforward(ctx, sleeves, base_cap)
    scenario_vehicles = vehicle_rollforward(ctx, sleeves, alt_cap)
    scenario_vehicles.insert(0, "scenario", alt_scn)
    complex_ = complex_rollforward(ctx, vehicles, sleeves)
    return RollForward(sleeves=sleeves, master=master, vehicles=vehicles, complex_=complex_, capital=base_cap,
                       scenario_vehicles=scenario_vehicles,
                       assumptions=[a.__dict__ for a in ctx.assumptions.describe()])
