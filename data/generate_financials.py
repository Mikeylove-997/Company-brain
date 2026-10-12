"""Generate the synthetic monthly P&L and KPI files for Ridgeline Outfitters (fictional).

Jan-Aug 2026 run close to budget with small noise. September 2026 carries the story the
analyst reports and meeting notes explain (wholesale order slip, freight surcharges,
marketing pulled forward, promo discounting) plus one variance that no document
explains (Software & IT), so the brain has something it must flag as unsupported.

Run: python data/generate_financials.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).parent / "financials"
MONTHS = pd.period_range("2026-01", "2026-09", freq="M")
rng = np.random.default_rng(42)


def noise(scale=0.015):
    return 1 + rng.normal(0, scale)


pl_rows, kpi_rows = [], []
for i, period in enumerate(MONTHS):
    m = str(period)
    season = 1 + 0.02 * i  # budgeted growth into fall

    b = {
        "Revenue - Wholesale": 1_150_000 * season,
        "Revenue - Direct-to-Consumer": 560_000 * season,
        "Revenue - Discounts & Returns": -48_000 * season,
    }
    gross_rev_b = b["Revenue - Wholesale"] + b["Revenue - Direct-to-Consumer"]
    b["COGS - Materials & Manufacturing"] = 0.38 * gross_rev_b
    b["COGS - Freight & Fulfillment"] = 0.060 * gross_rev_b
    b["OpEx - Salaries & Benefits"] = 320_000
    b["OpEx - Marketing"] = 115_000
    b["OpEx - Rent & Facilities"] = 80_000
    b["OpEx - Software & IT"] = 40_000
    b["OpEx - G&A Other"] = 52_000

    a = {k: v * noise() for k, v in b.items()}
    a["OpEx - Rent & Facilities"] = 80_000

    if m == "2026-08":  # early freight drift that September makes worse
        a["COGS - Freight & Fulfillment"] = 0.068 * (a["Revenue - Wholesale"] + a["Revenue - Direct-to-Consumer"])
    if m == "2026-09":
        a["Revenue - Wholesale"] = b["Revenue - Wholesale"] - 182_000 - 9_500  # Summit Retail PO slipped to Oct
        a["Revenue - Direct-to-Consumer"] = b["Revenue - Direct-to-Consumer"] * 1.054  # Fall Trail promo volume
        a["Revenue - Discounts & Returns"] = -88_400  # 20% promo + higher returns
        gross_rev_a = a["Revenue - Wholesale"] + a["Revenue - Direct-to-Consumer"]
        a["COGS - Materials & Manufacturing"] = 0.381 * gross_rev_a
        a["COGS - Freight & Fulfillment"] = 0.093 * gross_rev_a  # peak surcharge + expedited air
        a["OpEx - Salaries & Benefits"] = 334_600  # seasonal warehouse temps
        a["OpEx - Marketing"] = 158_900  # campaign pulled forward from October
        a["OpEx - Software & IT"] = 57_800  # no supporting document on purpose
        a["OpEx - G&A Other"] = 51_200

    for line, budget in b.items():
        category = line.split(" - ")[0].lower()
        pl_rows.append({"month": m, "category": category, "line_item": line,
                        "actual": round(a[line], 0), "budget": round(budget, 0)})

    units_b = gross_rev_b / 62
    gross_rev_a = a["Revenue - Wholesale"] + a["Revenue - Direct-to-Consumer"]
    kpis = {
        "Units shipped": (gross_rev_a / 62 * noise(0.01), units_b),
        "Freight cost per unit ($)": (a["COGS - Freight & Fulfillment"] / (gross_rev_a / 62), b["COGS - Freight & Fulfillment"] / units_b),
        "DTC promo discount rate (%)": (11.8 if m == "2026-09" else 6.0 * noise(0.05), 6.0),
        "DTC return rate (%)": (7.9 if m == "2026-09" else 5.5 * noise(0.05), 5.5),
        "Warehouse headcount (FTE)": (61 if m == "2026-09" else 52, 52),
        "Wholesale on-time ship rate (%)": (84.0 if m == "2026-09" else 96.0 * noise(0.01), 96.0),
    }
    for metric, (act, bud) in kpis.items():
        kpi_rows.append({"month": m, "metric": metric, "actual": round(act, 2), "budget": round(bud, 2)})

OUT.mkdir(exist_ok=True)
pd.DataFrame(pl_rows).to_csv(OUT / "pl_monthly.csv", index=False)
pd.DataFrame(kpi_rows).to_csv(OUT / "kpi_drivers.csv", index=False)
print(f"Wrote {len(pl_rows)} P&L rows and {len(kpi_rows)} KPI rows to {OUT}")
