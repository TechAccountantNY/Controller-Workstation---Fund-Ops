"""Fee recomputation per the fee schedule. Pure functions so they are trivially testable."""
from __future__ import annotations


def mgmt_fee_monthly(opening: float, closing: float, rate_pa: float) -> float:
    """1.25% p.a. on the average of beginning and ending monthly NAV, annual rate / 12."""
    return (opening + closing) / 2.0 * rate_pa / 12.0


def incentive_fee_monthly(opening: float, closing: float, hwm: float, rate: float) -> float:
    """15% of net new profits above the HWM, accrued monthly; nothing at or below HWM.

    Monthly accrual = rate x max(0, closing - max(opening, HWM)).
    If the sleeve ends the month at or below its HWM there is nothing to accrue.
    A negative month accrues nothing (no negative accrual is booked in-month; the
    annual crystallisation is where the cumulative figure is settled).
    """
    if closing <= hwm:
        return 0.0
    return rate * max(0.0, closing - max(opening, hwm))
