"""Reference data: the prior-month close package and the fee schedule.

These are the two things the month is reconstructed *against*. The fee schedule
is a Markdown document, so it is parsed rather than re-keyed — the rates and
HWMs used by the checks are the ones in the file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import PRIOR_CLOSE_DIR, FEE_SCHEDULE

# Sender domains → sleeve names. The sleeve universe itself comes from the prior
# close package; this table only tells the classifier who is writing.
MANAGER_DOMAINS = {
    "calderaridge.example": "Caldera Ridge Capital",
    "tidewatersys.example": "Tidewater Systematic",
    "foxglovepartners.example": "Foxglove Partners",
    "harrowgatecredit.example": "Harrowgate Credit Opportunities",
    "pellmarsh.example": "Pell & Marsh Quant",
    "stonebowgm.example": "Stonebow Global Macro",
    "juniperlane.example": "Juniper Lane Capital",
    "wrenfieldcm.example": "Wrenfield Capital Management",
}
ADMINISTRATOR_DOMAIN = "lanyardfs.example"
BANK_DOMAIN = "graniteharbor.example"
AUDITOR_DOMAIN = "whitlocksayre.example"
INTERNAL_DOMAIN = "brindlewood.example"

MASTER_NAME = "Brindlewood Master Fund Ltd"
CASH_SLEEVE = "Master cash & equivalents"


@dataclass
class FeeSchedule:
    management_fee_rate_pa: float          # 0.0125
    management_fee_basis: str              # text from the schedule
    incentive_rate: float                  # 0.15
    incentive_basis: str
    admin_fee_per_vehicle_month: float     # 4500
    hwm_as_of: str
    hwm: dict[str, float] = field(default_factory=dict)
    source_doc: str = ""


@dataclass
class Reference:
    sleeves: pd.DataFrame           # from master-sleeves-aug2026.csv (sleeve rows only)
    master_cash_prior: float
    master_total_prior: float
    vehicles: pd.DataFrame          # vehicles-aug2026.csv
    lookthrough: pd.DataFrame       # lookthrough-aug2026.csv
    investors: pd.DataFrame         # investor-register-aug2026.csv
    fees: FeeSchedule
    prior_close_docs: list[str]
    prior_close_cover_claim: str    # the cover note's assertion, kept for the cross-check


def _parse_fee_schedule(path: Path) -> FeeSchedule:
    text = path.read_text(encoding="utf-8")

    def pct_after(label: str) -> float:
        m = re.search(label + r".*?(\d+(?:\.\d+)?)%", text, re.S | re.I)
        if not m:
            raise ValueError(f"fee schedule: could not find rate for {label}")
        return float(m.group(1)) / 100.0

    mgmt_section = re.search(r"## Management fee\s*(.*?)\n##", text, re.S)
    inc_section = re.search(r"## Incentive fee\s*(.*?)\n##", text, re.S)
    admin_m = re.search(r"\$([\d,]+) per vehicle per month", text)
    hwm_asof = re.search(r"High-water marks as of ([^\n]+)", text)

    hwm: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(r"\|\s*([^|]+?)\s*\|\s*\$([\d,]+)\s*\|", line)
        if m and m.group(1).lower() not in ("sleeve", "---"):
            hwm[m.group(1)] = float(m.group(2).replace(",", ""))

    return FeeSchedule(
        management_fee_rate_pa=pct_after("## Management fee"),
        management_fee_basis=" ".join(mgmt_section.group(1).split()) if mgmt_section else "",
        incentive_rate=pct_after("## Incentive fee"),
        incentive_basis=" ".join(inc_section.group(1).split()) if inc_section else "",
        admin_fee_per_vehicle_month=float(admin_m.group(1).replace(",", "")) if admin_m else 0.0,
        hwm_as_of=hwm_asof.group(1).strip() if hwm_asof else "",
        hwm=hwm,
        source_doc=path.name,
    )


def load_reference(prior_close_dir: Path = PRIOR_CLOSE_DIR, fee_path: Path = FEE_SCHEDULE) -> Reference:
    sleeves_raw = pd.read_csv(prior_close_dir / "master-sleeves-aug2026.csv")
    is_total = sleeves_raw["sleeve"].str.contains("TOTAL", case=False)
    is_cash = sleeves_raw["sleeve"] == CASH_SLEEVE
    sleeves = sleeves_raw[~is_total & ~is_cash].copy().reset_index(drop=True)
    master_cash = float(sleeves_raw.loc[is_cash, "aug_close_nav_usd"].iloc[0])
    master_total = float(sleeves_raw.loc[is_total, "aug_close_nav_usd"].iloc[0])

    vehicles = pd.read_csv(prior_close_dir / "vehicles-aug2026.csv")
    lookthrough = pd.read_csv(prior_close_dir / "lookthrough-aug2026.csv")
    investors = pd.read_csv(prior_close_dir / "investor-register-aug2026.csv")
    cover = (prior_close_dir / "cover-note.txt").read_text(encoding="utf-8")
    claim = next((ln.strip() for ln in cover.splitlines() if "reconciled" in ln.lower()), "")

    return Reference(
        sleeves=sleeves,
        master_cash_prior=master_cash,
        master_total_prior=master_total,
        vehicles=vehicles,
        lookthrough=lookthrough,
        investors=investors,
        fees=_parse_fee_schedule(fee_path),
        prior_close_docs=sorted(p.name for p in prior_close_dir.iterdir()),
        prior_close_cover_claim=claim,
    )


def prior_close_nav(ref: Reference, sleeve: str) -> float:
    row = ref.sleeves.loc[ref.sleeves["sleeve"] == sleeve]
    if row.empty:
        raise KeyError(sleeve)
    return float(row["aug_close_nav_usd"].iloc[0])
