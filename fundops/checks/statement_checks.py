"""Manager statement checks: absence, opening-ties-to-prior-close, internal
arithmetic, fees against the schedule, and statement quality signals."""
from __future__ import annotations

from ..config import ARITH_TOLERANCE_USD, FEE_TOLERANCE_USD, STATEMENTS_DUE
from ..fees import expected_incentive_fee, expected_management_fee, implied_closing
from ..ingest.reference import prior_close_nav
from ..models import Exception_


def run_statement_checks(ctx):
    exceptions, passes = [], []
    fees = ctx.ref.fees
    pkg = "august-2026-close-package/master-sleeves-aug2026.csv"

    for sleeve in ctx.sleeves:
        prior = prior_close_nav(ctx.ref, sleeve)
        st = ctx.statement_for(sleeve)
        failure = next((f for f in ctx.statement_failures if f.get("sleeve") == sleeve), None)

        # ---- absence ---------------------------------------------------------
        if st is None:
            if failure:
                exceptions.append(Exception_(
                    key=f"statement:unparseable:{sleeve}", rank=0, severity="high", category="absence", area="statements",
                    counterparty=sleeve, title=f"{sleeve}: {ctx.period} statement received but could not be parsed",
                    detail=failure["error"], amount_impact_usd=prior, evidence=[failure["filename"]],
                    recommended_action="Map the document by hand and add a parser.", decision_required=True))
                continue
            other = [s for s in ctx.statements if s.sleeve == sleeve]
            other_txt = (f" The only {sleeve} document this month is its {other[0].period} statement "
                         f"({other[0].source_doc}), which notes the manager is 'transitioning systems this quarter'."
                         if other else "")
            share = prior / ctx.ref.master_total_prior
            exceptions.append(Exception_(
                key=f"statement:missing:{sleeve}", rank=0, severity="critical", category="absence", area="statements",
                counterparty=sleeve,
                title=f"{sleeve}: no {ctx.period} statement received",
                detail=(f"Statements were due to Lanyard by {STATEMENTS_DUE:%d %b}. Nothing from {sleeve} for {ctx.period} is in the inbox — "
                        f"no statement, no flash, no explanation. Prior close NAV ${prior:,.0f} is {share:.1%} of the master fund "
                        f"and has no September valuation at all.{other_txt}"),
                amount_impact_usd=prior, evidence=["(no document)", pkg] + [o.source_doc for o in other],
                findings=[f"expected: {ctx.period} statement from {sleeve}; received: none",
                          f"estimate policy '{ctx.assumptions.missing_statement_policy}': carried at prior close ${prior:,.0f}, 0.00% return, flagged"],
                recommended_action=f"Chase {sleeve} reporting today (phone, not email). Publish the estimate with the sleeve carried flat and disclosed; do not strike a final NAV without it.",
                decision_required=True))
            continue

        # ---- timeliness -------------------------------------------------------
        if st.received <= STATEMENTS_DUE:
            passes.append(dict(check="statement.received_by_due_date", subject=sleeve,
                               detail=f"received {st.received:%d %b} (due {STATEMENTS_DUE:%d %b}) via {st.extraction_method}", evidence=[st.source_doc]))

        # ---- opening ties to prior close --------------------------------------
        diff_open = st.opening_nav - prior
        if abs(diff_open) <= 1:
            passes.append(dict(check="statement.opening_ties_to_prior_close", subject=sleeve,
                               detail=f"opening {st.opening_nav:,.0f} = August close {prior:,.0f}", evidence=[st.source_doc, pkg]))
        else:
            alt_close = implied_closing(prior, st.return_pct)
            exceptions.append(Exception_(
                key=f"statement:opening_break:{sleeve}", rank=0, severity="critical", category="cross_document", area="statements",
                counterparty=sleeve,
                title=f"{sleeve}: opening balance does not tie to August close",
                detail=(f"Manager opening ${st.opening_nav:,.0f} vs Lanyard August close ${prior:,.0f} (Δ ${diff_open:,.0f}). "
                        f"The statement is internally consistent with its own opening, so either (a) ${-diff_open:,.0f} left the sleeve "
                        f"between the two documents (a redemption or fee the complex has no record of), or (b) the manager's "
                        f"system picked up the wrong opening" + (f" (the statement itself warns: '{st.notes}')" if st.notes else "") +
                        f". Applying the reported {st.return_pct:.2f}% to the "
                        f"correct opening gives ${alt_close:,.0f}, i.e. ${alt_close - st.closing_nav:,.0f} more than reported."),
                amount_impact_usd=alt_close - st.closing_nav,
                evidence=[st.source_doc, pkg],
                findings=[f"opening per manager {st.opening_nav:,.0f}; per prior close {prior:,.0f}; Δ {diff_open:,.0f}",
                          f"closing per manager {st.closing_nav:,.0f}; prior-close opening × (1+{st.return_pct:.2f}%) = {alt_close:,.0f}",
                          f"manager caveat: '{st.notes}'" if st.notes else "no caveat on statement"],
                recommended_action=f"Query {sleeve} for the opening-balance bridge before the estimate goes out; publish at the reported (lower) closing with the range disclosed.",
                decision_required=True))

        # ---- internal arithmetic ----------------------------------------------
        imp = implied_closing(st.opening_nav, st.return_pct)
        diff_arith = st.closing_nav - imp
        arith_ok = abs(diff_arith) <= ARITH_TOLERANCE_USD
        if arith_ok:
            passes.append(dict(check="statement.arithmetic", subject=sleeve,
                               detail=f"{st.opening_nav:,.0f} × (1 + {st.return_pct:.2f}%) = {imp:,.1f} ≈ closing {st.closing_nav:,.0f} [return read as {st.return_unit_inferred} from '{st.return_raw}']",
                               evidence=[st.source_doc]))

        # ---- fees vs schedule -------------------------------------------------
        fee_findings, fee_impact = [], 0.0
        if st.management_fee is not None:
            exp_m, how_m = expected_management_fee(st.opening_nav, st.closing_nav, fees.management_fee_rate_pa)
            d = st.management_fee - exp_m
            if abs(d) <= FEE_TOLERANCE_USD:
                passes.append(dict(check="statement.management_fee_per_schedule", subject=sleeve,
                                   detail=f"reported {st.management_fee:,.0f}; schedule {how_m}", evidence=[st.source_doc, "fee-schedule.md"]))
            else:
                alt = ""
                if abs(st.closing_nav * fees.management_fee_rate_pa / 4 - st.management_fee) <= FEE_TOLERANCE_USD:
                    alt = f" Reported figure equals closing NAV × {fees.management_fee_rate_pa:.2%} / 4 (a quarterly rate on closing NAV)."
                fee_findings.append(f"management fee reported {st.management_fee:,.0f} vs schedule {exp_m:,.0f} ({how_m}); Δ {d:,.0f}.{alt}"
                                    + (f" Manager's own note: '{st.notes}'." if st.notes else ""))
                fee_impact += d
        hwm = fees.hwm.get(sleeve)
        if st.incentive_fee is not None and hwm is not None:
            exp_i, how_i = expected_incentive_fee(st.opening_nav, st.closing_nav, hwm, fees.incentive_rate)
            d = st.incentive_fee - exp_i
            if abs(d) <= FEE_TOLERANCE_USD:
                passes.append(dict(check="statement.incentive_fee_per_schedule", subject=sleeve,
                                   detail=f"reported {st.incentive_fee:,.0f}; schedule {how_i}", evidence=[st.source_doc, "fee-schedule.md"]))
            else:
                fee_findings.append(f"incentive fee reported {st.incentive_fee:,.0f} vs schedule {exp_i:,.0f} ({how_i}); Δ {d:,.0f}."
                                    + (f" Manager's basis: '{st.notes.split(';')[0]}'." if st.notes else ""))
                fee_impact += d

        # ---- combine intra-document problems into one exception per sleeve ----
        findings = []
        if not arith_ok:
            findings.append(f"closing NAV {st.closing_nav:,.0f} ≠ opening {st.opening_nav:,.0f} × (1 + {st.return_pct:.2f}%) = {imp:,.0f}; Δ {diff_arith:,.0f}")
        findings += fee_findings
        if findings:
            sev = "high"
            title_bits = []
            if not arith_ok:
                title_bits.append("closing NAV does not agree with its own opening × return")
            if fee_findings:
                title_bits.append("fee accrual contrary to fee schedule")
            corr = ""
            if fee_impact > 0:
                corr = (f" If the fee is corrected, sleeve NAV would be ${fee_impact:,.0f} higher; the reported figure is used in the "
                        f"estimate (conservative) and the overcharge tracked for recovery.")
            exceptions.append(Exception_(
                key=f"statement:intra_doc:{sleeve}", rank=0, severity=sev, category="intra_document", area="statements",
                counterparty=sleeve, title=f"{sleeve}: " + " and ".join(title_bits),
                detail=" ".join(findings) + corr
                       + (f" Figures were sent in an email body; '{st.notes.split('; ')[-1]}'." if st.extraction_method == "email_body" and "formal" in st.notes.lower() else ""),
                amount_impact_usd=abs(diff_arith) + abs(fee_impact), evidence=[st.source_doc, "fee-schedule.md"],
                findings=findings,
                recommended_action=("Go back to the manager with the schedule basis and ask for a corrected statement" +
                                    (" and the administrator's formal statement" if st.extraction_method == "email_body" else "") + "."),
                decision_required=True))

        # ---- quality signals (low) --------------------------------------------
        quality = []
        if st.extraction_method == "email_body":
            quality.append("figures delivered in an email body rather than a statement document")
        if "estimate" in st.status_label and "final" in st.status_label:
            quality.append("labelled both 'estimate' and 'final'")
        if "unreconciled" in st.return_unit_inferred:
            quality.append(f"return '{st.return_raw}' could not be reconciled to the NAV movement under either unit")
        if quality and f"statement:intra_doc:{sleeve}" not in {e.key for e in exceptions}:
            exceptions.append(Exception_(
                key=f"statement:quality:{sleeve}", rank=0, severity="low", category="process", area="data_quality",
                counterparty=sleeve, title=f"{sleeve}: statement quality — " + "; ".join(quality),
                detail="Arithmetic and fees tie, so the number is usable for the estimate; the form of delivery is the issue for final NAV support.",
                amount_impact_usd=0, evidence=[st.source_doc],
                recommended_action="Ask for the formal statement document for the final NAV file.", decision_required=False))

    # ---- format heterogeneity (informational, one line) ----------------------
    units = {s.sleeve: (s.return_raw, s.return_unit_inferred) for s in ctx.statements if s.period == ctx.period}
    distinct = {u for _, u in units.values()}
    if len(distinct) > 1:
        exceptions.append(Exception_(
            key="statements:return_unit_heterogeneity", rank=0, severity="info", category="process", area="data_quality",
            title="Managers report 'net return' in three different units",
            detail="; ".join(f"{k}: '{raw}' read as {unit}" for k, (raw, unit) in units.items()) +
                   ". Units were inferred by reconciling each to its own NAV movement, so no figure was mis-scaled — but a hand process would have had to notice.",
            amount_impact_usd=0, evidence=[s.source_doc for s in ctx.statements if s.period == ctx.period],
            recommended_action="Keep the unit inference and record it; ask Tidewater to label the column.", decision_required=False))

    # ---- direct co-investment with no valuation -------------------------------
    lt = ctx.ref.lookthrough
    for _, d in lt[lt.via.str.contains("Direct co-investment")].iterrows():
        mgr_st = ctx.statement_for(d.manager)
        proxy = mgr_st.return_pct if mgr_st else 0.0
        exceptions.append(Exception_(
            key=f"valuation:direct_coinvest_missing:{d.vehicle}", rank=0, severity="medium", category="absence", area="statements",
            counterparty=d.manager,
            title=f"{d.vehicle}: no September valuation for its ${d.exposure_usd:,.0f} direct co-investment in {d.manager}",
            detail=(f"The {d.manager} statement covers the master sleeve (${prior_close_nav(ctx.ref, d.manager):,.0f} opening) only; nothing in the inbox "
                    f"values the direct position. Estimate policy '{ctx.assumptions.direct_coinvest_policy}': rolled at the sleeve return of {proxy:.2f}% "
                    f"(≈ ${d.exposure_usd * proxy / 100:,.0f} of P&L that is a proxy, not a statement)."),
            amount_impact_usd=d.exposure_usd * proxy / 100,
            evidence=["august-2026-close-package/lookthrough-aug2026.csv"] + ([mgr_st.source_doc] if mgr_st else []),
            recommended_action=f"Request the co-investment statement from {d.manager}; publish {d.vehicle} with the proxy disclosed.",
            decision_required=True))

    # ---- master cash: nothing in the pack verifies it -------------------------
    exceptions.append(Exception_(
        key="valuation:master_cash_unverified", rank=0, severity="info", category="absence", area="statements",
        title=f"Master cash (${ctx.ref.master_cash_prior:,.0f} at August) has no September bank statement in the pack",
        detail="Granite Harbor's 17 Sep notice says the September account analysis statement is 'available in the portal'; it was not retrieved. "
               f"Policy '{ctx.assumptions.master_cash_policy}': carried flat.",
        amount_impact_usd=0, evidence=["21_2026-09-17_monthly-account-analysis-statement-avail.txt", pkg],
        recommended_action="Pull the September bank statement from the portal before final NAV.", decision_required=False))

    return exceptions, passes
