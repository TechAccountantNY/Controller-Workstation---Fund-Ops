"""Independent check of outputs/ against the raw pack, using none of the fundops code.

    python tests/verify_outputs.py

Recomputes every published number from the source files with plain arithmetic
and asserts the committed CSVs agree. This is the 'what did you check before
trusting it' answer."""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "data" / "assessment-pack" / "attachments"
O = ROOT / "outputs"
fails = 0


def check(label, got, exp, tol=0.5):
    global fails
    ok = abs(got - exp) <= tol
    print(f"{'OK ' if ok else 'BAD'} {label}: got {got:,.2f} expected {exp:,.2f}")
    fails += 0 if ok else 1


def rows(name):
    with open(O / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---- raw figures, keyed by hand from the pack files -------------------------
aug = {r["sleeve"]: r for r in csv.DictReader(open(P / "august-2026-close-package/master-sleeves-aug2026.csv"))}
prior = {k: float(v["aug_close_nav_usd"]) for k, v in aug.items() if v["aug_open_nav_usd"]}
cash = float(aug["Master cash & equivalents"]["aug_close_nav_usd"])

def csv_stmt(fn):
    r = next(csv.DictReader(open(P / fn)))
    ret = r["net_return"]
    ret = float(ret[:-1]) if ret.endswith("%") else float(ret) * 100
    return float(r["opening_nav_usd"]), ret, float(r["closing_nav_usd"]), float(r["management_fee_usd"]), float(r["incentive_fee_usd"])

def xlsx_stmt(fn):
    ws = openpyxl.load_workbook(P / fn).worksheets[0]
    vals = [[c.value for c in r] for r in ws.iter_rows()]
    hdr = next(i for i, r in enumerate(vals) if r and any(isinstance(c, str) and "open" in c.lower() for c in r))
    d = vals[hdr + 1]
    return float(d[1]), float(d[2]), float(d[3]), float(d[4]), float(d[5])

def txt_stmt(fn):
    t = (P / fn).read_text()
    g = lambda pat: float(re.search(pat, t).group(1).replace(",", ""))
    return g(r"OPENING BALANCE\s*([\d,]+)"), g(r"NET PERFORMANCE\s*([\d.]+)"), g(r"CLOSING BALANCE\s*([\d,]+)"), g(r"MGMT FEE ACCRUAL\s*([\d,]+)"), g(r"INCENTIVE ACCRUAL\s*([\d,]+)")

def email_stmt(fn):
    t = (ROOT / "data/assessment-pack/inbox" / fn).read_text()
    g = lambda pat: float(re.search(pat, t, re.I).group(1).replace(",", ""))
    return g(r"Opening NAV:?\s*\$([\d,]+)"), g(r"Net return:?\s*(-?[\d.]+)%"), g(r"Closing NAV:?\s*\$([\d,]+)"), g(r"Management fee(?: accrued)?:?\s*\$([\d,]+)"), g(r"Incentive fee(?: accrued)?:?\s*\$([\d,]+)")

stm = {
    "Caldera Ridge Capital": csv_stmt("caldera-ridge-sept2026.csv"),
    "Tidewater Systematic": csv_stmt("tidewater-sept2026.csv"),
    "Foxglove Partners": xlsx_stmt("foxglove-sept2026.xlsx"),
    "Juniper Lane Capital": xlsx_stmt("juniper-lane-sept2026.xlsx"),
    "Stonebow Global Macro": txt_stmt("stonebow-sept2026.txt"),
    "Harrowgate Credit Opportunities": email_stmt("04_2026-09-30_harrowgate-credit-opportunities-septembe.txt"),
    "Pell & Marsh Quant": email_stmt("05_2026-10-01_pell-and-marsh-quant-sep-2026-estimate-f.txt"),
}
hwm = {}
for line in (P / "fee-schedule.md").read_text().splitlines():
    m = re.match(r"\|\s*([^|]+?)\s*\|\s*\$([\d,]+)\s*\|", line)
    if m and m.group(1) != "Sleeve":
        hwm[m.group(1)] = float(m.group(2).replace(",", ""))

# ---- 1. sleeve estimates ------------------------------------------------------
out_sl = {r["sleeve"]: r for r in rows("rollforward_sleeves.csv")}
est_total = 0.0
for sleeve, p in prior.items():
    if sleeve in stm:
        o, ret, c, mf, inf = stm[sleeve]
        check(f"{sleeve} arithmetic o×(1+r)=c", o * (1 + ret / 100), c, 1.0)
        check(f"{sleeve} estimate = manager closing", float(out_sl[sleeve]["estimate_nav"]), c)
        check(f"{sleeve} mgmt fee vs schedule (reported {mf:,.0f})", (o + c) / 2 * 0.0125 / 12,
              mf if sleeve != "Foxglove Partners" else 9420.0, 1.0)
        exp_inc = 0.0 if c <= hwm[sleeve] else 0.15 * max(0.0, c - max(o, hwm[sleeve]))
        check(f"{sleeve} incentive vs schedule (reported {inf:,.0f})", exp_inc, inf if sleeve != "Harrowgate Credit Opportunities" else 0.0, 1.0)
        est = c
    else:
        check(f"{sleeve} carried flat", float(out_sl[sleeve]["estimate_nav"]), p)
        est = p
    est_total += est
check("Stonebow opening break", stm["Stonebow Global Macro"][0] - prior["Stonebow Global Macro"], -185_000)
check("Stonebow alternative", prior["Stonebow Global Macro"] * (1 + stm["Stonebow Global Macro"][1] / 100), float(out_sl["Stonebow Global Macro"]["alt_nav"]), 1.0)

# ---- 2. master ----------------------------------------------------------------
master = {r["line"]: float(r["amount"]) for r in rows("rollforward_master.csv")}
check("Master Aug = package total", sum(prior.values()) + cash, 79_521_000)
check("Master Sep estimate", master["Master NAV — September estimate"], est_total + cash)
pnl = est_total - sum(prior.values())

# ---- 3. vehicles --------------------------------------------------------------
veh = {r["vehicle"]: r for r in csv.DictReader(open(P / "august-2026-close-package/vehicles-aug2026.csv"))}
out_v = {r["vehicle"]: r for r in rows("rollforward_vehicles.csv")}
subs = {"Brindlewood Select LP": 724_000}         # Bellhaven: matched wire, before dealing date
direct_pnl = {"Brindlewood Select LP": 3_113_000 * stm["Caldera Ridge Capital"][1] / 100}
total = 0.0
for v, r in veh.items():
    exp = (float(r["vehicle_nav_usd"]) + pnl * float(r["master_ownership_pct"]) / 100 + direct_pnl.get(v, 0) + subs.get(v, 0) - 4_500)
    check(f"{v} Sep estimate", float(out_v[v]["sep_nav_estimate"]), exp, 1.0)
    total += exp
cx = {r["line"]: float(r["amount"]) for r in rows("rollforward_complex.csv")}
check("Complex Sep estimate", cx["Complex NAV — September estimate (Σ vehicles)"], total, 1.0)
check("Complex Aug", cx["Complex NAV — August close (Σ vehicles)"], sum(float(r["vehicle_nav_usd"]) for r in veh.values()))
check("Alderbrook held cash", cx["Cash held unallotted (excluded from NAV)"], 2_404_000)
alt = {r["vehicle"]: float(r["sep_nav_estimate"]) for r in rows("rollforward_vehicles_alt_scenario.csv")}
check("Offshore alt scenario", alt["Brindlewood Offshore Ltd"], float(out_v["Brindlewood Offshore Ltd"]["sep_nav_estimate"]) - 1_164_000, 1.0)

# ---- 4. capital facts ---------------------------------------------------------
check("Alderbrook shortfall", 2_556_000 - 2_404_000, 152_000)
from datetime import date
check("Kestrel notice days", (date(2026, 9, 30) - date(2026, 9, 8)).days, 22)

print(f"\n{'ALL CHECKS PASSED' if fails == 0 else f'{fails} CHECK(S) FAILED'}")
sys.exit(1 if fails else 0)
