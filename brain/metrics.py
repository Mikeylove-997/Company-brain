"""All financial numbers are computed here, in Python. The LLM only ever quotes them."""
from dataclasses import dataclass

import pandas as pd

from . import config

SUBTOTALS = ["Net Revenue", "Gross Profit", "Operating Expenses", "Operating Profit"]

# Search hints per line item, used to target retrieval at the "why" behind each driver.
DRIVER_KEYWORDS = {
    "Revenue - Wholesale": "wholesale revenue purchase order customer ship date delayed",
    "Revenue - Direct-to-Consumer": "DTC direct-to-consumer revenue promotion traffic",
    "Revenue - Discounts & Returns": "promotion discount depth returns return rate sizing",
    "COGS - Materials & Manufacturing": "materials cost volume percentage of revenue",
    "COGS - Freight & Fulfillment": "freight fulfillment carrier surcharge expedited air shipping",
    "OpEx - Salaries & Benefits": "salaries temporary warehouse staff hiring headcount",
    "OpEx - Marketing": "marketing campaign spend pulled forward timing",
    "OpEx - Rent & Facilities": "rent facilities lease",
    "OpEx - Software & IT": "software IT subscriptions licenses systems spend",
    "OpEx - G&A Other": "general administrative expenses",
}


def load_pl(path=config.PL_PATH) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"month": str})


def load_kpis(path=config.KPI_PATH) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"month": str})


def months(pl: pd.DataFrame) -> list[str]:
    return sorted(pl["month"].unique())


def month_label(month: str) -> str:
    return pd.Period(month, freq="M").strftime("%B %Y")


def next_month_label(month: str) -> str:
    return (pd.Period(month, freq="M") + 1).strftime("%B %Y")


def _profit_impact(row) -> float:
    """Positive = helps profit. Revenue lines help when above budget; cost lines when below."""
    diff = row["actual"] - row["budget"]
    return diff if row["category"] == "revenue" else -diff


def line_variances(pl: pd.DataFrame, month: str) -> pd.DataFrame:
    df = pl[pl["month"] == month].copy()
    df["variance"] = df["actual"] - df["budget"]
    df["variance_pct"] = df["variance"] / df["budget"].abs() * 100
    df["profit_impact"] = df.apply(_profit_impact, axis=1)
    df["favorable"] = df["profit_impact"] >= 0
    return df.reset_index(drop=True)


def _subtotals(df: pd.DataFrame, col: str) -> dict[str, float]:
    by_cat = df.groupby("category")[col].sum()
    rev, cogs, opex = by_cat.get("revenue", 0), by_cat.get("cogs", 0), by_cat.get("opex", 0)
    return {"Net Revenue": rev, "Gross Profit": rev - cogs, "Operating Expenses": opex,
            "Operating Profit": rev - cogs - opex}


def pl_summary(pl: pd.DataFrame, month: str) -> pd.DataFrame:
    """Subtotal table (actual, budget, variance, variance %) plus margins for one month."""
    df = pl[pl["month"] == month]
    act, bud = _subtotals(df, "actual"), _subtotals(df, "budget")
    rows = []
    for name in SUBTOTALS:
        var = act[name] - bud[name]
        rows.append({"metric": name, "actual": act[name], "budget": bud[name], "variance": var,
                     "variance_pct": var / abs(bud[name]) * 100})
    for name in ["Gross Profit", "Operating Profit"]:
        a, b = act[name] / act["Net Revenue"] * 100, bud[name] / bud["Net Revenue"] * 100
        rows.append({"metric": f"{name.split()[0]} Margin %", "actual": a, "budget": b,
                     "variance": a - b, "variance_pct": None})
    return pd.DataFrame(rows)


def ytd_operating_profit(pl: pd.DataFrame, month: str) -> dict[str, float]:
    df = pl[pl["month"] <= month]
    act, bud = _subtotals(df, "actual")["Operating Profit"], _subtotals(df, "budget")["Operating Profit"]
    return {"actual": act, "budget": bud, "variance": act - bud, "variance_pct": (act - bud) / abs(bud) * 100}


def top_drivers(pl: pd.DataFrame, month: str, materiality=config.MATERIALITY_USD) -> pd.DataFrame:
    """Line items whose variance moved profit by at least `materiality`, biggest hit first."""
    df = line_variances(pl, month)
    df = df[df["profit_impact"].abs() >= materiality]
    return df.sort_values("profit_impact").reset_index(drop=True)


def line_trend(pl: pd.DataFrame, line_item: str, month: str, n=3) -> pd.DataFrame:
    df = pl[(pl["line_item"] == line_item) & (pl["month"] <= month)].tail(n).copy()
    df["variance_pct"] = (df["actual"] - df["budget"]) / df["budget"].abs() * 100
    return df[["month", "actual", "budget", "variance_pct"]]


def kpi_variances(kpis: pd.DataFrame, month: str) -> pd.DataFrame:
    df = kpis[kpis["month"] == month].copy()
    df["variance"] = df["actual"] - df["budget"]
    df["variance_pct"] = df["variance"] / df["budget"].abs() * 100
    return df.reset_index(drop=True)


def fmt_usd(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}${x / 1e6:.2f}M" if x >= 1e6 else f"{sign}${x / 1e3:.1f}K"


def fmt_pct(x) -> str:
    return "n/a" if x is None or pd.isna(x) else f"{x:+.1f}%"


@dataclass
class MonthMetrics:
    month: str
    summary: pd.DataFrame
    lines: pd.DataFrame
    drivers: pd.DataFrame
    kpis: pd.DataFrame
    ytd: dict

    def to_prompt_text(self, pl: pd.DataFrame) -> str:
        """Plain-text metrics block handed to the LLM. Every number it may quote is in here."""
        out = [f"P&L SUMMARY - {month_label(self.month)} (actual vs budget)"]
        for r in self.summary.itertuples():
            if "Margin" in r.metric:
                out.append(f"- {r.metric}: actual {r.actual:.1f}%, budget {r.budget:.1f}%, change {r.variance:+.1f} pts")
            else:
                out.append(f"- {r.metric}: actual {fmt_usd(r.actual)}, budget {fmt_usd(r.budget)}, "
                           f"variance {fmt_usd(r.variance)} ({fmt_pct(r.variance_pct)})")
        y = self.ytd
        out.append(f"- YTD Operating Profit: actual {fmt_usd(y['actual'])}, budget {fmt_usd(y['budget'])}, "
                   f"variance {fmt_usd(y['variance'])} ({fmt_pct(y['variance_pct'])})")

        out.append(f"\nMATERIAL VARIANCE DRIVERS (profit impact >= {fmt_usd(config.MATERIALITY_USD)}, worst first)")
        for r in self.drivers.itertuples():
            trend = line_trend(pl, r.line_item, self.month)
            trend_txt = ", ".join(f"{t.month}: {fmt_pct(t.variance_pct)}" for t in trend.itertuples())
            out.append(f"- {r.line_item}: actual {fmt_usd(r.actual)}, budget {fmt_usd(r.budget)}, "
                       f"variance {fmt_usd(r.variance)} ({fmt_pct(r.variance_pct)}), "
                       f"profit impact {fmt_usd(r.profit_impact)} [{'favorable' if r.favorable else 'unfavorable'}]; "
                       f"last 3 months vs budget: {trend_txt}")

        out.append("\nOPERATING KPIs")
        for r in self.kpis.itertuples():
            out.append(f"- {r.metric}: actual {r.actual:,.1f}, budget {r.budget:,.1f} ({fmt_pct(r.variance_pct)})")
        return "\n".join(out)


def compute(pl: pd.DataFrame, kpis: pd.DataFrame, month: str) -> MonthMetrics:
    return MonthMetrics(month=month, summary=pl_summary(pl, month), lines=line_variances(pl, month),
                        drivers=top_drivers(pl, month), kpis=kpi_variances(kpis, month),
                        ytd=ytd_operating_profit(pl, month))
