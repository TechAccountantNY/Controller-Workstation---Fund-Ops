"""Does the prior close package tie to itself? If the starting point is wrong,
everything rolled from it is wrong, so this runs first.

Also: does the administrator's claim that August was "reconciled to manager
statements" survive contact with the one August manager statement in the inbox?
"""
from __future__ import annotations

from ..fees import implied_closing
from ..models import Exception_

TOL = 1000.0  # package figures are rounded to $1,000


def run_prior_close_checks(ctx):
    ref = ctx.ref
    exceptions, passes = [], []
    pkg = "august-2026-close-package/"

    # 1. sleeves + cash = master total
    sleeve_sum = float(ref.sleeves["aug_close_nav_usd"].sum())
    total = sleeve_sum + ref.master_cash_prior
    if abs(total - ref.master_total_prior) <= 1:
        passes.append(dict(check="prior_close.master_total", subject="Master Fund",
                           detail=f"Σ sleeves {sleeve_sum:,.0f} + cash {ref.master_cash_prior:,.0f} = {total:,.0f} = package total {ref.master_total_prior:,.0f}",
                           evidence=[pkg + "master-sleeves-aug2026.csv"]))
    else:
        exceptions.append(Exception_(key="prior_close:master_total", rank=0, severity="critical", category="intra_document",
                                     area="prior_period", title="August master total does not foot",
                                     detail=f"Σ sleeves + cash = {total:,.0f} vs package total {ref.master_total_prior:,.0f}",
                                     amount_impact_usd=total - ref.master_total_prior,
                                     evidence=[pkg + "master-sleeves-aug2026.csv"], recommended_action="Query Lanyard", decision_required=False))

    # 2. each sleeve: open x (1+ret) ≈ close (rounded to $1,000 in the package)
    for _, r in ref.sleeves.iterrows():
        imp = implied_closing(float(r.aug_open_nav_usd), float(r.aug_net_return_pct))
        diff = float(r.aug_close_nav_usd) - imp
        if abs(diff) <= TOL:
            passes.append(dict(check="prior_close.sleeve_arithmetic", subject=r.sleeve,
                               detail=f"{r.aug_open_nav_usd:,.0f} × (1 + {r.aug_net_return_pct}%) = {imp:,.0f} ≈ {r.aug_close_nav_usd:,.0f} (Δ {diff:,.0f}, within $1,000 rounding)",
                               evidence=[pkg + "master-sleeves-aug2026.csv"]))
        else:
            exceptions.append(Exception_(key=f"prior_close:arith:{r.sleeve}", rank=0, severity="medium", category="intra_document",
                                         area="prior_period", title=f"August arithmetic break — {r.sleeve}",
                                         detail=f"{r.aug_open_nav_usd:,.0f} × (1 + {r.aug_net_return_pct}%) = {imp:,.0f} vs {r.aug_close_nav_usd:,.0f}",
                                         amount_impact_usd=diff, evidence=[pkg + "master-sleeves-aug2026.csv"],
                                         recommended_action="Query Lanyard", decision_required=False, counterparty=r.sleeve))

    # 3. vehicles: ownership % x master = master interest; components = vehicle NAV; Σ vehicles' master interest = master total
    vi_sum = 0.0
    for _, v in ref.vehicles.iterrows():
        exp_interest = ref.master_total_prior * float(v.master_ownership_pct) / 100.0
        comp = float(v.master_interest_usd) + float(v.direct_investments_usd) + float(v.cash_usd)
        vi_sum += float(v.master_interest_usd)
        ok1 = abs(exp_interest - float(v.master_interest_usd)) <= TOL
        ok2 = abs(comp - float(v.vehicle_nav_usd)) <= 1
        (passes if ok1 else exceptions).append(
            dict(check="prior_close.vehicle_ownership", subject=v.vehicle,
                 detail=f"{v.master_ownership_pct}% × {ref.master_total_prior:,.0f} = {exp_interest:,.0f} ≈ master interest {v.master_interest_usd:,.0f}",
                 evidence=[pkg + "vehicles-aug2026.csv"]) if ok1 else
            Exception_(key=f"prior_close:ownership:{v.vehicle}", rank=0, severity="high", category="intra_document", area="prior_period",
                       title=f"August ownership % does not tie — {v.vehicle}", detail=f"{exp_interest:,.0f} vs {v.master_interest_usd:,.0f}",
                       amount_impact_usd=exp_interest - float(v.master_interest_usd), evidence=[pkg + "vehicles-aug2026.csv"],
                       recommended_action="Query Lanyard", decision_required=False))
        (passes if ok2 else exceptions).append(
            dict(check="prior_close.vehicle_components", subject=v.vehicle,
                 detail=f"master {v.master_interest_usd:,.0f} + direct {v.direct_investments_usd:,.0f} + cash {v.cash_usd:,.0f} = {comp:,.0f} = vehicle NAV {v.vehicle_nav_usd:,.0f}",
                 evidence=[pkg + "vehicles-aug2026.csv"]) if ok2 else
            Exception_(key=f"prior_close:components:{v.vehicle}", rank=0, severity="high", category="intra_document", area="prior_period",
                       title=f"August vehicle NAV does not foot — {v.vehicle}", detail=f"{comp:,.0f} vs {v.vehicle_nav_usd:,.0f}",
                       amount_impact_usd=comp - float(v.vehicle_nav_usd), evidence=[pkg + "vehicles-aug2026.csv"],
                       recommended_action="Query Lanyard", decision_required=False))
    if abs(vi_sum - ref.master_total_prior) <= 1:
        passes.append(dict(check="prior_close.vehicles_sum_to_master", subject="Complex",
                           detail=f"Σ vehicles' master interest {vi_sum:,.0f} = master total {ref.master_total_prior:,.0f}",
                           evidence=[pkg + "vehicles-aug2026.csv", pkg + "master-sleeves-aug2026.csv"]))

    # 4. look-through: per vehicle, Σ exposure via master = ownership % x Σ sleeves (ex cash)
    lt = ref.lookthrough
    for _, v in ref.vehicles.iterrows():
        via = lt[(lt.vehicle == v.vehicle) & (lt.via == "Master Fund")]["exposure_usd"].sum()
        exp = sleeve_sum * float(v.master_ownership_pct) / 100.0
        ok = abs(float(via) - exp) <= TOL
        (passes if ok else exceptions).append(
            dict(check="prior_close.lookthrough", subject=v.vehicle,
                 detail=f"Σ look-through via master {via:,.0f} = {v.master_ownership_pct}% × Σ sleeves {sleeve_sum:,.0f} = {exp:,.0f}",
                 evidence=[pkg + "lookthrough-aug2026.csv"]) if ok else
            Exception_(key=f"prior_close:lookthrough:{v.vehicle}", rank=0, severity="medium", category="cross_document", area="prior_period",
                       title=f"August look-through does not tie — {v.vehicle}", detail=f"{via:,.0f} vs {exp:,.0f}", amount_impact_usd=float(via) - exp,
                       evidence=[pkg + "lookthrough-aug2026.csv"], recommended_action="Query Lanyard", decision_required=False))
    direct = lt[lt.via.str.contains("Direct co-investment")]
    for _, d in direct.iterrows():
        v = ref.vehicles.loc[ref.vehicles.vehicle == d.vehicle].iloc[0]
        ok = abs(float(d.exposure_usd) - float(v.direct_investments_usd)) <= 1
        (passes if ok else exceptions).append(
            dict(check="prior_close.direct_coinvest", subject=d.vehicle,
                 detail=f"direct co-investment in {d.manager} {d.exposure_usd:,.0f} = vehicle direct investments {v.direct_investments_usd:,.0f}",
                 evidence=[pkg + "lookthrough-aug2026.csv", pkg + "vehicles-aug2026.csv"]) if ok else
            Exception_(key=f"prior_close:direct:{d.vehicle}", rank=0, severity="medium", category="cross_document", area="prior_period",
                       title="August direct co-investment does not tie", detail=f"{d.exposure_usd:,.0f} vs {v.direct_investments_usd:,.0f}",
                       amount_impact_usd=0, evidence=[pkg + "lookthrough-aug2026.csv"], recommended_action="Query Lanyard", decision_required=False))

    # 5. HWM table in the fee schedule agrees with the package
    for _, r in ref.sleeves.iterrows():
        sched = ctx.ref.fees.hwm.get(r.sleeve)
        ok = sched is not None and abs(sched - float(r.high_water_mark_usd)) <= 1
        (passes if ok else exceptions).append(
            dict(check="prior_close.hwm_agrees_with_fee_schedule", subject=r.sleeve,
                 detail=f"HWM {r.high_water_mark_usd:,.0f} in package = {sched:,.0f} in fee schedule", evidence=[pkg + "master-sleeves-aug2026.csv", "fee-schedule.md"]) if ok else
            Exception_(key=f"prior_close:hwm:{r.sleeve}", rank=0, severity="medium", category="cross_document", area="prior_period",
                       title=f"HWM differs between package and fee schedule — {r.sleeve}", detail=f"package {r.high_water_mark_usd:,.0f} vs schedule {sched}",
                       amount_impact_usd=0, evidence=[pkg + "master-sleeves-aug2026.csv", "fee-schedule.md"], recommended_action="Confirm HWM", decision_required=False))

    # 6. the cover note's claim vs the one August manager statement in the inbox
    for st in ctx.statements:
        if st.period == "Aug-2026":
            row = ref.sleeves.loc[ref.sleeves.sleeve == st.sleeve].iloc[0]
            diffs = []
            if abs(st.opening_nav - float(row.aug_open_nav_usd)) > TOL:
                diffs.append(f"opening NAV: manager {st.opening_nav:,.0f} vs package {row.aug_open_nav_usd:,.0f} (Δ {st.opening_nav - float(row.aug_open_nav_usd):,.0f})")
            if abs(st.return_pct - float(row.aug_net_return_pct)) > 0.005:
                diffs.append(f"net return: manager {st.return_pct:.2f}% vs package {row.aug_net_return_pct:.2f}%")
            close_ok = abs(st.closing_nav - float(row.aug_close_nav_usd)) <= TOL
            if close_ok:
                passes.append(dict(check="prior_close.manager_aug_closing_agrees", subject=st.sleeve,
                                   detail=f"manager August closing {st.closing_nav:,.0f} = package {row.aug_close_nav_usd:,.0f}",
                                   evidence=[st.source_doc, pkg + "master-sleeves-aug2026.csv"]))
            else:
                diffs.append(f"closing NAV: manager {st.closing_nav:,.0f} vs package {row.aug_close_nav_usd:,.0f}")
            if diffs:
                exceptions.append(Exception_(
                    key=f"prior_period:manager_vs_package:{st.sleeve}", rank=0, severity="medium", category="cross_document",
                    area="prior_period", counterparty=st.sleeve,
                    title=f"August: {st.sleeve}'s own statement disagrees with Lanyard's 'reconciled' package",
                    detail=("Cover note states: '" + ref.prior_close_cover_claim + "'. " + "; ".join(diffs) +
                            (". Closing NAV agrees, so the September opening is not affected — but the August return "
                             "published to investors and the fee base may be. Manager also notes it is 'transitioning systems'." if close_ok else "")),
                    amount_impact_usd=st.opening_nav - float(row.aug_open_nav_usd),
                    evidence=[st.source_doc, pkg + "master-sleeves-aug2026.csv", pkg + "cover-note.txt"],
                    findings=diffs,
                    recommended_action="Ask Lanyard which August opening/return they reconciled to and get Wrenfield to confirm; no change to September opening.",
                    decision_required=False))
    return exceptions, passes
