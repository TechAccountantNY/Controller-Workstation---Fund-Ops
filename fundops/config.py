"""Paths, period and the explicit modelling assumptions.

Every assumption that affects a published number lives here, with a reason, so
it can be challenged in review rather than discovered in code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data" / "assessment-pack"
INBOX_DIR = DATA_DIR / "inbox"
ATTACH_DIR = DATA_DIR / "attachments"
PRIOR_CLOSE_DIR = ATTACH_DIR / "august-2026-close-package"
FEE_SCHEDULE = ATTACH_DIR / "fee-schedule.md"
OUTPUT_DIR = REPO_ROOT / "outputs"
STATE_DB = REPO_ROOT / "state" / "workstation.sqlite"
SEED_DECISIONS = REPO_ROOT / "decisions" / "seed_decisions.json"

# The month being reconstructed and the "as of" date of the reconstruction.
PERIOD = "Sep-2026"
PERIOD_START = date(2026, 9, 1)
PERIOD_END = date(2026, 9, 30)
AS_OF = date(2026, 10, 1)            # T. Reeve's "where are we?" — the morning after month-end
STATEMENTS_DUE = date(2026, 10, 1)   # Lanyard close calendar: manager statements due 1 Oct
ESTIMATE_DUE = date(2026, 10, 2)     # Lanyard close calendar: vehicle estimates published 2 Oct
CAPITAL_CONFIRM_DUE = date(2026, 10, 1)

# Tolerances
ARITH_TOLERANCE_USD = 2.0            # rounding on opening x (1+return) vs closing
FEE_TOLERANCE_USD = 2.0              # rounding on fee recomputation
INVESTOR_QUERY_SLA_DAYS = 5          # business-day SLA before an unanswered query is an exception


@dataclass(frozen=True)
class Assumption:
    key: str
    statement: str
    reason: str


@dataclass
class Assumptions:
    """Modelling choices for the estimate. Each is a judgment call, not a fact."""

    missing_statement_policy: str = "carry_flat"
    # carry_flat: a sleeve with no statement is carried at prior-close NAV (0% return) and flagged.

    direct_coinvest_policy: str = "sleeve_return_proxy"
    # sleeve_return_proxy: a direct co-investment with no valuation is rolled at the
    # same manager's sleeve return for the month and flagged.

    stonebow_policy: str = "reported_closing"
    # reported_closing: use the manager's reported closing NAV (lower) and disclose the
    # alternative (prior-close opening x reported return) as a range.

    master_cash_policy: str = "carry_flat"
    # No master bank statement in the pack; cash carried at prior close.

    subscription_cash_policy: str = "hold_in_vehicle_cash"
    # Accepted subscription proceeds sit in the vehicle's cash at month-end (no master
    # P&L participation in the month of dealing). Pending/unmatched proceeds are excluded
    # from NAV entirely.

    pnl_allocation_basis: str = "prior_close_ownership"
    # Master P&L for the month is allocated to vehicles on prior-close ownership %.

    admin_fee_accrual: bool = True
    # $4,500 per vehicle per month accrued (billed quarterly in arrears).

    kestrel_redemption_scenario: str = "deferred"
    # deferred: not processed at 30 Sep (notice period not met) — base case.
    # as_queued: processed at 30 Sep as Lanyard TA queued it — alternative shown alongside.

    def describe(self) -> list[Assumption]:
        return [
            Assumption("missing_statement_policy", self.missing_statement_policy,
                       "No September figures exist for the sleeve; prior close is the last audited-by-admin number."),
            Assumption("direct_coinvest_policy", self.direct_coinvest_policy,
                       "The co-investment is in the same manager/strategy; the sleeve return is the best available proxy."),
            Assumption("stonebow_policy", self.stonebow_policy,
                       "Manager's own closing figure is the conservative (lower) of the two candidates until the opening-balance break is explained."),
            Assumption("master_cash_policy", self.master_cash_policy,
                       "No master bank statement is in the pack; the Granite Harbor analysis statement was not retrieved."),
            Assumption("subscription_cash_policy", self.subscription_cash_policy,
                       "No evidence of a sweep to master; mid-month equalisation would need daily NAVs the pack doesn't contain."),
            Assumption("pnl_allocation_basis", self.pnl_allocation_basis,
                       "No capital moved at master level during the month under the policies above."),
            Assumption("admin_fee_accrual", str(self.admin_fee_accrual),
                       "Fee schedule: $4,500 per vehicle per month, flat."),
            Assumption("kestrel_redemption_scenario", self.kestrel_redemption_scenario,
                       "8 Sep notice for 30 Sep is 22 days against a 30-day requirement; processing needs a documented waiver."),
        ]


DEFAULT_ASSUMPTIONS = Assumptions()
