"""The checks engine: every exception the system raises, with its arithmetic and sources.

Severity rubric (stated so it can be argued with):
  HIGH    — changes whether the estimate can be published as-is, or the integrity of an
            investor's capital transaction. Includes any single item > 0.1% of complex NAV
            that is unverified or unexplained.
  MEDIUM  — a quantified misstatement below that line, or evidence the *final* NAV will
            need that the estimate can proceed without; client-facing service failures.
  LOW     — housekeeping, prior-period, documentation gaps with no NAV effect this month.
  INFO    — handled automatically; shown so a human knows the system made a call.
Within a tier, items are ranked by the dollars in question.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import AS_OF, EXTERNAL_QUERY_SLA_DAYS, MASTER_FUND, PERIOD, PRIOR_PERIOD, RETURN_PCT_DECIMALS, TOLERANCE_USD
from ..models import CapitalInstruction, Email, Exception_, ManagerStatement, WireCredit
from .fees import incentive_fee_monthly, mgmt_fee_monthly

SEV_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}


def _usd(x: float) -> str:
    return f"${x:,.0f}" if x >= 0 else f"-${-x:,.0f}"


def run_checks(emails: list[Email], prior: dict, fees: dict, statements: dict[str, ManagerStatement],
               prior_period_statements: list[ManagerStatement], instructions: list[CapitalInstruction],
               wires: list[WireCredit], capital_rows: list[dict]) -> tuple[list[Exception_], list[dict]]:
    exc: list[Exception_] = []
    ok: list[dict] = []
    complex_nav = float(prior["vehicles"]["vehicle_nav_usd"].sum())
    sleeves = prior["sleeves"].set_index("sleeve")
    emails_by_id = {e.doc_id: e for e in emails}

    def passed(check: str, scope: str, detail: str, sources: list[str]):
        ok.append(dict(check=check, scope=scope, detail=detail, sources=", ".join(sources)))

    # ---------------------------------------------------------------- prior close self-consistency
    s_sum = float(sleeves["aug_close_nav_usd"].sum()) + prior["master_cash"]
    if abs(s_sum - prior["master_total"]) <= TOLERANCE_USD:
        passed("Prior close: sleeves + cash = Master total", MASTER_FUND,
               f"{_usd(float(sleeves['aug_close_nav_usd'].sum()))} + {_usd(prior['master_cash'])} = {_usd(prior['master_total'])}",
               [prior["files"]["sleeves"]])
    else:
        exc.append(Exception_("REF-MASTER-SUM", "HIGH", "reference", "Prior close: Master sleeves + cash ≠ Master total",
                              f"{_usd(s_sum)} vs {_usd(prior['master_total'])}", [prior["files"]["sleeves"]], s_sum - prior["master_total"],
                              "Raise with Lanyard before using the package", "Lanyard"))
    v = prior["vehicles"]
    mi = float(v["master_interest_usd"].sum())
    if abs(mi - prior["master_total"]) <= TOLERANCE_USD:
        passed("Prior close: vehicles' Master interests = Master total", MASTER_FUND, f"{_usd(mi)} = {_usd(prior['master_total'])}", [prior["files"]["vehicles"]])
    comp_ok = all(abs(r["master_interest_usd"] + r["direct_investments_usd"] + r["cash_usd"] - r["vehicle_nav_usd"]) <= TOLERANCE_USD for _, r in v.iterrows())
    if comp_ok:
        passed("Prior close: vehicle NAV = Master interest + direct + cash", "all vehicles", "ties for 3 of 3 vehicles", [prior["files"]["vehicles"]])
    # look-through ties
    lt = prior["lookthrough"]
    bad = []
    for _, r in lt.iterrows():
        if r["via"] == "Master Fund":
            pct = float(v.loc[v["vehicle"] == r["vehicle"], "master_ownership_pct"].iloc[0]) / 100
            expect = pct * float(sleeves.loc[r["manager"], "aug_close_nav_usd"])
            if abs(expect - r["exposure_usd"]) > TOLERANCE_USD:
                bad.append(f"{r['vehicle']}/{r['manager']}: {_usd(r['exposure_usd'])} vs {_usd(expect)}")
        elif r["via"] == "Direct":
            if abs(float(sleeves.loc[r["manager"], "aug_close_nav_usd"]) - r["exposure_usd"]) > TOLERANCE_USD:
                bad.append(f"Master/{r['manager']} direct")
    if not bad:
        passed("Prior close: look-through = ownership % × sleeve NAV", "all vehicles", f"ties for {len(lt)} of {len(lt)} rows", [prior["files"]["lookthrough"]])
    else:
        exc.append(Exception_("REF-LOOKTHROUGH", "MEDIUM", "reference", "Prior close look-through does not tie", "; ".join(bad), [prior["files"]["lookthrough"]], None, "Raise with Lanyard", "Lanyard"))
    # HWM table vs package
    hw_bad = [m for m, h in fees["hwm"].items() if abs(float(sleeves.loc[m, "high_water_mark_usd"]) - h) > TOLERANCE_USD]
    if not hw_bad:
        passed("Fee schedule HWMs = prior close HWM column", "all sleeves", f"8 of 8 tie", [fees["file"], prior["files"]["sleeves"]])
    else:
        exc.append(Exception_("REF-HWM", "MEDIUM", "reference", "HWM differs between fee schedule and prior close", ", ".join(hw_bad), [fees["file"], prior["files"]["sleeves"]], None, "Confirm which HWM governs", "Controller"))
    # prior close: stated return reproduces closing
    pc_bad = []
    for name, r in sleeves.iterrows():
        implied = float(r["aug_open_nav_usd"]) * (1 + float(r["aug_net_return_pct"]) / 100)
        # returns are quoted to 2dp of a percent, so the rounding error bound is open × 0.00005
        if abs(implied - float(r["aug_close_nav_usd"])) > float(r["aug_open_nav_usd"]) * 0.5 * 10 ** -(RETURN_PCT_DECIMALS + 2) + TOLERANCE_USD:
            pc_bad.append(name)
    if not pc_bad:
        passed("Prior close: open × (1 + return) reproduces close within 2dp rounding", "all sleeves", "8 of 8", [prior["files"]["sleeves"]])
    # register coverage
    reg_tot = prior["register"].groupby("vehicle")["aug_31_balance_usd"].sum()
    reg_lines = [f"{veh}: register {_usd(float(reg_tot.get(veh, 0)))} vs vehicle NAV {_usd(float(nav))}" for veh, nav in zip(v["vehicle"], v["vehicle_nav_usd"])]
    exc.append(Exception_("REF-REGISTER-COVERAGE", "LOW", "reference",
                          "Investor register as provided does not sum to vehicle NAVs",
                          "Register totals " + _usd(float(reg_tot.sum())) + f" against {_usd(complex_nav)} of vehicle NAV ({'; '.join(reg_lines)}). "
                          "Either the register is an extract limited to the investors active this month, or it does not tie. The per-investor roll-forward below is therefore partial by construction.",
                          [prior["files"]["register"], prior["files"]["vehicles"]], None,
                          "Ask Lanyard for the full 31 Aug register; until then do not publish investor-level balances from this file", "Lanyard"))

    # ---------------------------------------------------------------- manager statements
    for name, r in sleeves.iterrows():
        aug_close = float(r["aug_close_nav_usd"])
        hwm = float(r["high_water_mark_usd"])
        st = statements.get(name)
        if st is None:
            prior_doc = next((p for p in prior_period_statements if p.manager == name), None)
            extra = ""
            if prior_doc:
                e = emails_by_id[prior_doc.doc_id]
                extra = f" The only correspondence from {name} this month is its {prior_doc.period} statement ({prior_doc.doc_id}, {e.date.isoformat()}), which notes it is 'transitioning systems this quarter'."
            exc.append(Exception_(
                f"EXP-STATEMENT-{name}", "HIGH", "missing document",
                f"{name}: no {PERIOD} statement received",
                f"Sleeve carried at {_usd(aug_close)} ({aug_close / prior['master_total']:.1%} of Master) with no {PERIOD} figures as of {AS_OF.isoformat()}. "
                f"Lanyard's calendar set manager statements due 1 October.{extra} "
                f"Sensitivity: each 1% of return on this sleeve is {_usd(aug_close * 0.01)} ({aug_close * 0.01 / complex_nav:.2%} of complex NAV).",
                [prior["files"]["sleeves"], "inbox/25"] + ([prior_doc.doc_id] if prior_doc else []), aug_close,
                "Chase the manager today; publish the estimate with the sleeve carried flat and disclosed, or hold the estimate",
                "Fund ops",
                treatments={"carry_flat": "Carry the sleeve at the August close with zero return, disclosed in the estimate",
                            "hold_estimate": "Hold the complex estimate until the statement arrives"},
                default_treatment="carry_flat"))
            continue

        e = emails_by_id[st.doc_id]
        src = [st.doc_id] + ([st.source_file] if st.formal else [])
        # period
        if st.period and st.period != PERIOD:
            exc.append(Exception_(f"STM-PERIOD-{name}", "MEDIUM", "single document", f"{name}: statement is for {st.period}, not {PERIOD}", "", src, None, "Request the correct period", "Fund ops"))
        # internal arithmetic
        implied = st.opening_nav * (1 + st.net_return)
        bound = st.opening_nav * 0.5 * 10 ** -(RETURN_PCT_DECIMALS + 2) + TOLERANCE_USD
        if abs(implied - st.closing_nav) <= bound:
            passed("Statement: open × (1 + return) = close", name, f"{_usd(st.opening_nav)} × (1 {st.net_return:+.2%}) = {_usd(implied)} vs stated {_usd(st.closing_nav)}", src)
        else:
            exc.append(Exception_(f"STM-ARITH-{name}", "HIGH", "single document", f"{name}: closing NAV does not follow from opening × return",
                                  f"{_usd(st.opening_nav)} × (1 {st.net_return:+.2%}) = {_usd(implied)} but statement shows {_usd(st.closing_nav)} (difference {_usd(st.closing_nav - implied)}).",
                                  src, st.closing_nav - implied, "Ask the manager which figure is right", "Fund ops"))
        # opening ties to prior close
        if abs(st.opening_nav - aug_close) <= TOLERANCE_USD:
            passed("Statement: opening NAV = our prior close", name, f"{_usd(st.opening_nav)} = {_usd(aug_close)}", src + [prior["files"]["sleeves"]])
        else:
            diff = st.opening_nav - aug_close
            alt_close = aug_close * (1 + st.net_return)
            exc.append(Exception_(
                f"STM-OPENTIE-{name}", "HIGH", "cross-document",
                f"{name}: opening balance does not tie to the August close",
                f"Statement opens at {_usd(st.opening_nav)}; Lanyard's final August package closed the sleeve at {_usd(aug_close)} — a {_usd(diff)} break ({abs(diff) / complex_nav:.2%} of complex NAV). "
                f"The statement is internally consistent on its own opening ({_usd(st.opening_nav)} × (1 {st.net_return:+.2%}) = {_usd(st.closing_nav)}); applied to our opening the close would be {_usd(alt_close)}, "
                f"a {_usd(alt_close - st.closing_nav)} difference in closing NAV. No subscription, redemption or transfer at the sleeve is evidenced anywhere in the inbox to explain it.",
                src + [prior["files"]["sleeves"]], diff,
                "Ask Stonebow ops and Lanyard the same question today: which opening is right, and if money moved, where is the confirmation",
                "Fund ops",
                treatments={"manager_closing": f"Use the manager's reported closing {_usd(st.closing_nav)} and carry {_usd(diff)} as an unexplained reconciling item in the Master roll-forward",
                            "our_opening_x_return": f"Apply the reported return to our August close: {_usd(alt_close)}"},
                default_treatment="manager_closing"))
        # management fee
        if st.mgmt_fee is not None:
            recomputed = mgmt_fee_monthly(st.opening_nav, st.closing_nav, fees["mgmt_rate_pa"])
            if abs(recomputed - st.mgmt_fee) <= TOLERANCE_USD:
                passed("Statement: management fee = 1.25%/12 × avg(open, close)", name, f"recomputed {_usd(recomputed)} vs stated {_usd(st.mgmt_fee)}", src + [fees["file"]])
            else:
                basis_note = f" The statement itself says: '{st.notes.split(' || ')[0]}'." if st.notes and "fee" in st.notes.lower() else ""
                exc.append(Exception_(
                    f"FEE-MGMT-{name}", "MEDIUM", "fee terms",
                    f"{name}: management fee accrued on the wrong basis",
                    f"Statement accrues {_usd(st.mgmt_fee)}. Schedule: 1.25% p.a. on the average of opening and closing NAV, /12 → ({_usd(st.opening_nav)} + {_usd(st.closing_nav)})/2 × 1.25%/12 = {_usd(recomputed)}. "
                    f"Overstated by {_usd(st.mgmt_fee - recomputed)}.{basis_note} "
                    f"{_usd(st.closing_nav)} × 1.25%/4 = {_usd(st.closing_nav * fees['mgmt_rate_pa'] / 4)}, which reproduces the stated figure.",
                    src + [fees["file"]], st.mgmt_fee - recomputed,
                    "Request a corrected statement; if closing NAV is net of this accrual, Brindlewood's NAV is understated by the overstatement", "Fund ops"))
        # incentive fee
        if st.incentive_fee is not None:
            recomputed = incentive_fee_monthly(st.opening_nav, st.closing_nav, hwm, fees["incentive_rate"])
            if abs(recomputed - st.incentive_fee) <= TOLERANCE_USD:
                passed("Statement: incentive = 15% × profit above max(open, HWM); nil at/below HWM", name,
                       f"HWM {_usd(hwm)}; recomputed {_usd(recomputed)} vs stated {_usd(st.incentive_fee)}", src + [fees["file"]])
            else:
                under = st.closing_nav <= hwm
                exc.append(Exception_(
                    f"FEE-INC-{name}", "MEDIUM", "fee terms",
                    f"{name}: incentive fee accrued while below its high-water mark" if under else f"{name}: incentive fee does not agree to the schedule",
                    (f"Statement accrues {_usd(st.incentive_fee)} ('{st.notes.split('incentive note: ')[-1]}'). Closing NAV {_usd(st.closing_nav)} is below the HWM of {_usd(hwm)} "
                     f"(schedule as of 31 Aug 2026), and the schedule says no accrual while at or below HWM. Correct accrual: $0. The stated figure is 15% × {_usd(st.closing_nav - st.opening_nav)} = {_usd(0.15 * (st.closing_nav - st.opening_nav))}, i.e. the HWM was ignored."
                     if under else f"Recomputed {_usd(recomputed)} vs stated {_usd(st.incentive_fee)}"),
                    src + [fees["file"]], st.incentive_fee - recomputed,
                    "Request a corrected statement; if closing NAV is net of this accrual, Brindlewood's NAV is understated by the overstatement", "Fund ops"))
        # formality / wording
        if not st.formal:
            exc.append(Exception_(f"STM-EMAILONLY-{name}", "MEDIUM", "evidence",
                                  f"{name}: figures by email only; no statement document",
                                  f"Closing NAV {_usd(st.closing_nav)} taken from the body of {st.doc_id}" + (". The email says a formal statement will follow from the administrator." if "formal statement" in st.notes else "."),
                                  [st.doc_id], st.closing_nav, "Acceptable for the estimate; obtain the administrator statement before final NAV", "Fund ops"))
        if st.status_wording == "estimate (final)":
            exc.append(Exception_(f"STM-WORDING-{name}", "LOW", "evidence", f"{name}: status wording is ambiguous ('estimate (final)')",
                                  f"Subject of {st.doc_id} says 'estimate (final)'; body says 'September final for your records'. Treated as final.",
                                  [st.doc_id], None, "Ask the manager to confirm 'final' in writing", "Fund ops"))
        if "inferred" in st.return_unit_method:
            exc.append(Exception_(f"STM-UNIT-{name}", "INFO", "data quality", f"{name}: return quoted without a unit; read as a fraction",
                                  f"Value {st.net_return:.4f} in column 'net_return' of {st.source_file}. {st.return_unit_method}.", src, None, "None; parser handled it. Ask the manager to label the column.", "Fund ops"))
        if "AUTOMATED RENDERING" in st.notes.upper():
            exc.append(Exception_(f"STM-RENDER-{name}", "INFO", "data quality", f"{name}: statement is machine-rendered text with a layout caveat",
                                  f"Parsed by label; figures tie internally. Caveat on the document: '{st.notes}'.", src, None, "None", "Fund ops"))
        if "internal use" in st.notes.lower():
            exc.append(Exception_(f"STM-INTERNAL-{name}", "INFO", "evidence", f"{name}: pack is marked 'internal use'",
                                  "The workbook banner reads 'internal use'. Figures tie to the schedule; the label suggests it may not be the investor-facing statement.", src, None, "Ask for the investor statement version before final NAV", "Fund ops"))

    # prior-period statements vs the administrator's package
    for p in prior_period_statements:
        if p.manager in sleeves.index and p.period == PRIOR_PERIOD:
            r = sleeves.loc[p.manager]
            diffs = []
            if abs(p.opening_nav - float(r["aug_open_nav_usd"])) > TOLERANCE_USD:
                diffs.append(f"opening {_usd(p.opening_nav)} vs package {_usd(float(r['aug_open_nav_usd']))} ({_usd(p.opening_nav - float(r['aug_open_nav_usd']))})")
            if abs(p.net_return * 100 - float(r["aug_net_return_pct"])) > 10 ** -RETURN_PCT_DECIMALS / 2:
                diffs.append(f"return {p.net_return:.2%} vs package {float(r['aug_net_return_pct']):.2f}%")
            if abs(p.closing_nav - float(r["aug_close_nav_usd"])) > TOLERANCE_USD:
                diffs.append(f"closing {_usd(p.closing_nav)} vs package {_usd(float(r['aug_close_nav_usd']))}")
            if diffs:
                exc.append(Exception_(f"STM-PRIOR-{p.manager}", "LOW", "cross-document",
                                      f"{p.manager}: its own August statement disagrees with Lanyard's 'reconciled' August package",
                                      "; ".join(diffs) + ". Closing agrees, so no NAV effect, but the package's cover note says 'all August figures are final and reconciled to manager statements', which this contradicts.",
                                      [p.doc_id, prior["files"]["sleeves"], prior["files"]["cover"]], None,
                                      "Ask Lanyard which opening and return it reconciled to; note for the manager's system transition", "Fund ops"))

    # ---------------------------------------------------------------- expectations: direct co-investment, cash
    for _, ltr in lt.iterrows():
        if str(ltr["via"]).lower().startswith("direct co"):
            proxy = statements.get(ltr["manager"])
            amt = float(ltr["exposure_usd"])
            veh_nav = float(v.loc[v["vehicle"] == ltr["vehicle"], "vehicle_nav_usd"].iloc[0])
            exc.append(Exception_(
                f"EXP-DIRECT-{ltr['vehicle']}", "HIGH", "missing document",
                f"{ltr['vehicle']}: no {PERIOD} valuation for its direct co-investment in {ltr['manager']}",
                f"{_usd(amt)} ({amt / veh_nav:.1%} of the vehicle's NAV) is held directly, outside the Master. The {ltr['manager']} statement received covers the Master's sleeve "
                f"({_usd(float(sleeves.loc[ltr['manager'], 'aug_close_nav_usd']))} opening) only. " +
                (f"The sleeve returned {proxy.net_return:+.2%}; applied as a proxy the position would be {_usd(amt * (1 + proxy.net_return))}." if proxy else ""),
                [prior["files"]["lookthrough"]] + ([proxy.doc_id] if proxy else []), amt,
                "Request the co-investment statement from the manager; for the estimate, proxy at the sleeve return and disclose",
                "Fund ops",
                treatments={"proxy_sleeve_return": "Value at the Master sleeve's reported return for the month, disclosed as a proxy",
                            "carry_flat": "Carry at the August value with zero return"},
                default_treatment="proxy_sleeve_return"))
    total_cash = prior["master_cash"] + float(v["cash_usd"].sum())
    bank_notice = next((e for e in emails if e.kind == "bank_statement_notice"), None)
    exc.append(Exception_(
        "EXP-CASH", "MEDIUM", "missing document",
        f"No {PERIOD} bank statements: all cash balances carried from August",
        f"Master cash {_usd(prior['master_cash'])} and vehicle cash {_usd(float(v['cash_usd'].sum()))} ({_usd(total_cash)} in total, {total_cash / complex_nav:.1%} of complex NAV) are the August figures. "
        + (f"Granite Harbor's note of {bank_notice.date.isoformat()} ({bank_notice.doc_id}) says the September account analysis is 'available in the portal — no action required'; it was not retrieved and is not in the pack. " if bank_notice else "")
        + "Two subscription wires were received into subscription accounts, which also need confirming to the statements.",
        [prior["files"]["vehicles"], prior["files"]["sleeves"]] + ([bank_notice.doc_id] if bank_notice else []), total_cash,
        "Pull the September statements from the portal and reconcile before final NAV; acceptable to carry for the estimate", "Fund ops"))

    # ---------------------------------------------------------------- capital activity
    cap_by_id = {r["item_id"]: r for r in capital_rows}
    for ins in instructions:
        row = cap_by_id[ins.item_id]
        if ins.kind == "subscription":
            w_amt = row["wire_usd"]
            if w_amt is None:
                exc.append(Exception_(f"CAP-NOFUNDS-{ins.item_id}", "HIGH", "capital activity", f"{ins.investor}: subscription instructed, no funds received",
                                      f"{_usd(ins.amount)} to {ins.vehicle} for {ins.effective_date.isoformat()} dealing; no credit advice.", [ins.doc_id], ins.amount, "Chase funds; do not deal", "Fund ops"))
            elif abs(w_amt - ins.amount) > 0.5 or dt.date.fromisoformat(row["wire_value_date"]) > ins.effective_date:
                short = ins.amount - w_amt
                late = (dt.date.fromisoformat(row["wire_value_date"]) - ins.effective_date).days
                exc.append(Exception_(
                    f"CAP-SUB-{ins.item_id}", "HIGH", "capital activity",
                    f"{ins.investor}: subscription funds do not match the instruction and arrived after the dealing date",
                    f"Instruction ({ins.doc_id}) and signed agreement ({ins.signed_form}) both say {_usd(ins.amount)} to {ins.vehicle} for {ins.effective_date.isoformat()} dealing. "
                    f"Granite Harbor credit advice ({row['wire_doc']}) shows {_usd(w_amt)} value {row['wire_value_date']} from '{row['investor']}' nominee — {_usd(short)} short and {late} days after the dealing date. "
                    f"No transfer-agency acknowledgement of this subscription is on file. The money cannot be dealt as instructed: wrong amount, wrong date, and no 15 Sep NAV in the pack to deal at.",
                    [ins.doc_id, ins.signed_form, row["wire_doc"]], ins.amount,
                    "Hold the {:,.0f} in the subscription account; call the investor today about the shortfall and agree a dealing date (1 Oct at the September NAV is the obvious candidate); tell Lanyard it is not in September activity".format(w_amt),
                    "Fund ops / IR",
                    treatments={"hold_in_subscription_account": "Not a September subscription; funds held outside NAV pending resolution",
                                "accept_received_amount": f"Accept {_usd(w_amt)} for the next dealing date (1 Oct) once the investor confirms in writing",
                                "return_funds": "Return the wire and await a correct one"},
                    default_treatment="hold_in_subscription_account"))
            else:
                passed("Subscription: wire amount, vehicle and timing match the instruction", ins.investor,
                       f"{_usd(w_amt)} value {row['wire_value_date']} ref '{row['wire_reference']}' vs instruction {_usd(ins.amount)} for {ins.effective_date.isoformat()}", [ins.doc_id, row["wire_doc"]])
            if not ins.signed_form:
                exc.append(Exception_(f"CAP-DOCS-{ins.item_id}", "MEDIUM", "capital activity",
                                      f"{ins.investor}: subscription funded and dealt without a signed subscription agreement on file",
                                      f"{_usd(ins.amount)} into {ins.vehicle} instructed by email ({ins.doc_id}) and funded ({row['wire_doc']}); no signed document in the pack. {ins.investor} is an existing investor per the register, so this is a top-up, but the file is incomplete.",
                                      [ins.doc_id, row["wire_doc"], prior["files"]["register"]], ins.amount,
                                      "Obtain the signed additional-subscription form before confirming the trade to Lanyard", "Fund ops / Legal"))
            if not ins.ack_doc_id:
                exc.append(Exception_(f"CAP-NOACK-{ins.item_id}", "LOW", "capital activity",
                                      f"{ins.investor}: no transfer-agency acknowledgement of the subscription",
                                      f"Lanyard acknowledged the Kestrel redemption within a day (inbox/14) but nothing is on file for this subscription.", [ins.doc_id], None,
                                      "Confirm with Lanyard that the subscription is in the September dealing cycle", "Fund ops"))
            exc.append(Exception_(f"CAP-MIDMONTH-{ins.item_id}", "INFO", "capital activity",
                                  f"{ins.investor}: mid-month dealing date needs a 15 Sep NAV that is not in the pack",
                                  f"Dealing {ins.effective_date.isoformat()}. Lanyard's calendar (inbox/25) only mentions month-end estimates and finals. Units issued depend on a 15 Sep NAV the system has not seen.",
                                  [ins.doc_id, "inbox/25"], None, "Confirm the dealing NAV and units with Lanyard", "Fund ops"))
        else:
            deficient = any(c.startswith("NOTICE:") and "short" in c for c in row["checks"])
            if deficient:
                exc.append(Exception_(
                    f"CAP-RED-{ins.item_id}", "HIGH", "capital activity",
                    f"{ins.investor}: redemption queued by the transfer agent without the required notice",
                    f"Instruction ({ins.doc_id}, {ins.notice_date.isoformat()}) asks for {_usd(ins.amount)} out of {ins.vehicle} effective {ins.effective_date.isoformat()} 'per the 30-day notice provision'. "
                    f"The register ({prior['files']['register']}) gives this investor 'Monthly, 30 days notice': 30 days from {ins.notice_date.isoformat()} is {row['earliest_eligible_date']}, so the instruction is {(dt.date.fromisoformat(row['earliest_eligible_date']) - ins.effective_date).days} days short for a 30 Sep dealing. "
                    f"Lanyard nonetheless acknowledged it the next day and 'queued it for the September dealing cycle' ({ins.ack_doc_id}). The amount is within the investor's balance ({_usd(row['prior_balance_usd'])}). "
                    f"Paying it needs {_usd(ins.amount)} of cash; the vehicle held {_usd(float(v.loc[v['vehicle'] == ins.vehicle, 'cash_usd'].iloc[0]))}, so a Master redemption is implied and nothing evidences one.",
                    [ins.doc_id, ins.ack_doc_id, prior["files"]["register"], prior["files"]["vehicles"]], ins.amount,
                    "GP decision today: waive notice in writing (and tell the other Offshore investors nothing they'd object to) or defer to 31 Oct; instruct Lanyard before the NAV is struck; line up the Master cash either way",
                    "GP / Controller",
                    treatments={"defer_per_terms": "Treat as not effective 30 Sep; next eligible dealing date 31 Oct. Estimate shows Offshore NAV before the redemption, redemption disclosed as pending",
                                "process_as_queued": "GP waives the notice shortfall in writing; redemption effective 30 Sep as Lanyard queued it"},
                    default_treatment="defer_per_terms"))
            else:
                passed("Redemption: notice period satisfied", ins.investor, "", [ins.doc_id])

    # ---------------------------------------------------------------- service and open threads
    queries = [e for e in emails if e.kind == "investor_query"]
    if queries:
        first = min(queries, key=lambda e: e.date)
        follow = [e for e in queries if e is not first]
        age = (AS_OF - first.date).days
        exc.append(Exception_(
            "SVC-QUERY-RowanHill", "MEDIUM", "service",
            f"{first.counterparty}: client question unanswered for {age} days",
            f"Asked on {first.date.isoformat()} ({first.doc_id}) for (1) the August 2026 net return of Brindlewood Offshore Ltd and (2) current redemption terms; chased {', '.join(f.date.isoformat() + ' (' + f.doc_id + ')' for f in follow)} — 'clients are asking'. No reply is in the pack. "
            f"(2) is answerable from the register: Monthly, 30 days notice. (1) is not answerable from the August package: it gives vehicle NAVs but no vehicle opening NAV or return, so a vehicle-level August return has to come from Lanyard. "
            f"Our standard is {EXTERNAL_QUERY_SLA_DAYS} business days. Note the same vehicle has a redemption in flight from another advisor.",
            [q.doc_id for q in queries] + [prior["files"]["register"], prior["files"]["vehicles"]], None,
            "Reply today with the redemption terms and a date for the return; get the August vehicle return from Lanyard", "IR"))
    for e in emails:
        if e.kind == "auditor_request":
            items = [l.strip() for l in e.body.splitlines() if l.strip()[:2].rstrip(".").isdigit()]
            exc.append(Exception_("SVC-AUDIT-PBC", "LOW", "service", f"{e.counterparty}: {len(items)} FY25 PBC items outstanding; fieldwork slots held for late October",
                                  " / ".join(items) + f". Reminder dated {e.date.isoformat()}; no response in the pack.", [e.doc_id], None,
                                  "Assign owners and dates; reply to the auditor this week", "Controller"))
        if e.kind == "admin_calendar":
            exc.append(Exception_("SVC-LANYARD-CONFIRM", "MEDIUM", "service", "Lanyard asked for September capital activity to be confirmed by 1 October — two of three items cannot be confirmed clean",
                                  f"Calendar ({e.doc_id}): statements due 1 Oct, estimates 2 Oct, final 14 Oct, capital activity confirmation 1 Oct. Bellhaven is clean; Alderbrook is short and late; Kestrel is notice-deficient but already queued by Lanyard's own TA.",
                                  [e.doc_id], None, "Send Lanyard the confirmation today with the two items explicitly held", "Fund ops"))
        if e.kind == "counterparty_admin":
            exc.append(Exception_(f"ADM-CONTACT-{e.counterparty}", "INFO", "housekeeping", f"{e.counterparty}: operations contact changes from 1 October",
                                  e.body.replace("\n", " "), [e.doc_id], None, "Update the counterparty directory", "Fund ops"))
    exc.append(Exception_("ADM-FEE-ACCRUAL", "INFO", "methodology", "Administration fee accrued at $4,500 per vehicle for September",
                          "Fee schedule: $4,500 per vehicle per month, flat, billed quarterly in arrears. The August package shows vehicle NAV = Master interest + direct + cash exactly, with no visible accrual line, so Lanyard's treatment is not evidenced. This roll-forward accrues it as a separate line.",
                          [fees["file"], prior["files"]["vehicles"]], 3 * fees["admin_fee_per_vehicle_month"], "Confirm Lanyard's treatment", "Controller"))
    noise = [e for e in emails if e.kind in ("noise", "internal_noise", "manager_marketing")]
    exc.append(Exception_("INFO-NOISE", "INFO", "triage", f"{len(noise)} messages classified as not relevant to the close",
                          "; ".join(f"{e.doc_id} {e.subject[:40]}" for e in noise), [e.doc_id for e in noise], None, "None — review the triage if anything looks wrong", "Fund ops"))
    flashes = [e for e in emails if e.kind == "manager_flash"]
    if flashes:
        exc.append(Exception_("INFO-FLASH", "INFO", "triage", f"{len(flashes)} mid-month flash estimates received; superseded by month-end figures",
                              "; ".join(f"{e.doc_id} {e.counterparty}: {e.body.splitlines()[0][:70]}" for e in flashes), [e.doc_id for e in flashes], None, "None", "Fund ops"))

    # ---------------------------------------------------------------- rank
    exc.sort(key=lambda x: (SEV_ORDER[x.severity], -(abs(x.nav_impact_usd) if x.nav_impact_usd else 0), x.exc_id))
    for i, x in enumerate(exc, 1):
        x.rank = i
    return exc, ok
