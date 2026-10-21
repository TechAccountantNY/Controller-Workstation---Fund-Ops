"""Run with:  python -m unittest -v   (pytest also works)."""
from __future__ import annotations

import unittest
from datetime import date

from fundops.checks.capital_checks import next_eligible_dealing_date
from fundops.fees import expected_incentive_fee, expected_management_fee
from fundops.ingest.statements import detect_period, infer_return_pct
from fundops.pipeline import run


class FeeTests(unittest.TestCase):
    def test_management_fee_average_nav(self):
        fee, _ = expected_management_fee(13_551_000, 13_743_424, 0.0125)
        self.assertAlmostEqual(fee, 14_216, delta=1)

    def test_management_fee_catches_quarterly_on_closing(self):
        fee, _ = expected_management_fee(8_952_000, 9_134_621, 0.0125)
        self.assertAlmostEqual(fee, 9_420, delta=1)
        self.assertNotAlmostEqual(fee, 28_546, delta=1)

    def test_incentive_above_hwm(self):
        fee, _ = expected_incentive_fee(13_551_000, 13_743_424, 12_873_000, 0.15)
        self.assertAlmostEqual(fee, 28_864, delta=1)

    def test_incentive_below_hwm_is_zero(self):
        fee, why = expected_incentive_fee(8_856_000, 8_955_187, 9_210_000, 0.15)
        self.assertEqual(fee, 0.0)
        self.assertIn("no accrual", why)

    def test_incentive_negative_month_is_zero(self):
        fee, _ = expected_incentive_fee(7_548_000, 7_517_053, 7_171_000, 0.15)
        self.assertEqual(fee, 0.0)

    def test_incentive_crossing_hwm_only_counts_above_hwm(self):
        fee, _ = expected_incentive_fee(9_000_000, 9_500_000, 9_210_000, 0.15)
        self.assertAlmostEqual(fee, 0.15 * (9_500_000 - 9_210_000))


class ReturnUnitTests(unittest.TestCase):
    def test_percent_string(self):
        self.assertEqual(infer_return_pct("1.42%", 13_551_000, 13_743_424), (1.42, "percent_string"))

    def test_decimal_inferred_from_nav(self):
        r, unit = infer_return_pct(0.0207, 11_867_000, 12_112_647)
        self.assertAlmostEqual(r, 2.07)
        self.assertEqual(unit, "decimal")

    def test_percent_number_inferred_from_nav(self):
        r, unit = infer_return_pct(2.04, 8_952_000, 9_134_621)
        self.assertEqual((r, unit), (2.04, "percent"))

    def test_pct_suffix(self):
        self.assertEqual(infer_return_pct("2.27 PCT", 10_708_000, 10_951_072), (2.27, "percent_string"))

    def test_unreconciled_is_flagged_not_fixed(self):
        _, unit = infer_return_pct(5.0, 10_000_000, 10_100_000)
        self.assertIn("unreconciled", unit)

    def test_detect_period(self):
        self.assertEqual(detect_period("Wrenfield — August 2026 final statement"), "Aug-2026")
        self.assertEqual(detect_period("Sep-26 monthly file"), "Sep-2026")
        self.assertEqual(detect_period("SEPTEMBER2026"), "Sep-2026")


class NoticeTests(unittest.TestCase):
    def test_monthly_30_days(self):
        self.assertEqual(next_eligible_dealing_date(date(2026, 9, 8), "Monthly", 30), date(2026, 10, 31))

    def test_quarterly_45_days(self):
        self.assertEqual(next_eligible_dealing_date(date(2026, 9, 8), "Quarterly", 45), date(2026, 12, 31))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run()

    def test_all_messages_classified(self):
        self.assertEqual(len(self.r.ctx.messages), 28)
        self.assertFalse([m for m in self.r.ctx.messages if m.category == "unclassified"])

    def test_statements_parsed(self):
        sep = [s for s in self.r.ctx.statements if s.period == "Sep-2026"]
        self.assertEqual(len(sep), 7)
        self.assertEqual(self.r.ctx.statement_failures, [])
        self.assertIsNone(self.r.ctx.statement_for("Wrenfield Capital Management"))

    def test_expected_exceptions_present(self):
        keys = {e.key for e in self.r.checks.exceptions}
        for k in ("statement:missing:Wrenfield Capital Management", "statement:opening_break:Stonebow Global Macro",
                  "capital:redemption:RED-13", "capital:subscription:SUB-11", "statement:intra_doc:Foxglove Partners",
                  "statement:intra_doc:Harrowgate Credit Opportunities", "valuation:direct_coinvest_missing:Brindlewood Select LP",
                  "correspondence:unanswered:10", "prior_period:manager_vs_package:Wrenfield Capital Management"):
            self.assertIn(k, keys)

    def test_clean_sleeves_raise_nothing(self):
        bad = [e.key for e in self.r.checks.exceptions if e.area == "statements" and any(
            n in e.key for n in ("Caldera", "Tidewater", "Juniper"))]
        self.assertEqual(bad, [])

    def test_ranking_is_severity_first(self):
        sev = [e.severity for e in self.r.checks.exceptions]
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        self.assertEqual(sev, sorted(sev, key=order.get))

    def test_rollforward_foots(self):
        roll = self.r.roll
        self.assertAlmostEqual(roll.master.set_index("line").amount["Master NAV — August close"], 79_521_000)
        self.assertAlmostEqual(roll.vehicles.aug_nav.sum(), 83_621_000)
        cx = roll.complex_.set_index("line").amount
        self.assertAlmostEqual(cx["Complex NAV — September estimate (Σ vehicles)"], roll.vehicles.sep_nav_estimate.sum())
        # vehicles' master P&L shares sum to master P&L
        self.assertAlmostEqual(roll.vehicles.master_pnl_share.sum(), roll.sleeves.pnl_estimate.sum(), places=2)

    def test_held_cash_excluded_from_nav(self):
        v = self.r.roll.vehicles.set_index("vehicle")
        self.assertEqual(v.loc["Brindlewood Partners LP", "subscriptions"], 0.0)
        self.assertEqual(v.loc["Brindlewood Partners LP", "cash_held_unallotted"], 2_404_000)
        self.assertEqual(v.loc["Brindlewood Select LP", "subscriptions"], 724_000)

    def test_scenario_differs_only_for_offshore(self):
        base = self.r.roll.vehicles.set_index("vehicle").sep_nav_estimate
        alt = self.r.roll.scenario_vehicles.set_index("vehicle").sep_nav_estimate
        self.assertAlmostEqual(alt["Brindlewood Offshore Ltd"], base["Brindlewood Offshore Ltd"] - 1_164_000)
        self.assertAlmostEqual(alt["Brindlewood Partners LP"], base["Brindlewood Partners LP"])


if __name__ == "__main__":
    unittest.main()
