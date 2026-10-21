"""Fee recomputation under the fee schedule. Pure functions, unit-tested.

The schedule says:
  * management fee: rate p.a. on the average of beginning and ending monthly NAV, /12
  * incentive fee: 15% of net new profits, accrued monthly only above the HWM;
    no accrual while NAV is at or below HWM.
"""
from __future__ import annotations


def expected_management_fee(opening: float, closing: float, rate_pa: float) -> tuple[float, str]:
    avg = (opening + closing) / 2.0
    fee = avg * rate_pa / 12.0
    return fee, f"avg({opening:,.0f}, {closing:,.0f}) = {avg:,.1f} × {rate_pa:.2%} / 12 = {fee:,.0f}"


def expected_incentive_fee(opening: float, closing: float, hwm: float, rate: float) -> tuple[float, str]:
    if closing <= hwm:
        return 0.0, f"closing {closing:,.0f} ≤ HWM {hwm:,.0f} → no accrual"
    base = max(opening, hwm)
    gain = closing - base
    if gain <= 0:
        return 0.0, f"closing {closing:,.0f} − max(opening, HWM) {base:,.0f} ≤ 0 → no net new profit, no accrual"
    fee = rate * gain
    return fee, f"{rate:.0%} × (closing {closing:,.0f} − max(opening {opening:,.0f}, HWM {hwm:,.0f})) = {rate:.0%} × {gain:,.0f} = {fee:,.0f}"


def implied_closing(opening: float, return_pct: float) -> float:
    return opening * (1.0 + return_pct / 100.0)
