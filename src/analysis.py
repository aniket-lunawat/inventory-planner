"""
All the numbers behind the dashboard.

  abc()            which products bring in the money
  monthly_sales()  revenue trend
  idle_stock()     money stuck in stock that isn't selling
  reorder_table()  what to reorder now, and how much
  forecast()       expected units next month, with a backtest of its accuracy

Run on its own to print a summary:  python src/analysis.py
"""
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

import config as cfg


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
# Which database to read. Normally data/inventory.db; the dashboard switches it
# to an uploaded company's database. It is stored per thread, because the web
# app serves each visitor on their own thread: one visitor's upload must never
# change what another visitor sees.
_active = threading.local()


def use_db(path=None) -> None:
    """Point every query at this database (None = the default one)."""
    _active.db = Path(path) if path else None


def db_path() -> Path:
    return getattr(_active, "db", None) or cfg.DB_PATH


def run_sql(name: str, params: dict | None = None) -> pd.DataFrame:
    sql = (cfg.SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")
    with sqlite3.connect(db_path()) as con:
        return pd.read_sql_query(sql, con, params=params or {})


def today() -> pd.Timestamp:
    """The 'as of' date of the stock count. Everything is measured back from it."""
    return _today(str(db_path()))


@lru_cache(maxsize=32)
def _today(path: str) -> pd.Timestamp:
    with sqlite3.connect(path) as con:
        return pd.Timestamp(con.execute("SELECT MAX(as_of_date) FROM stock").fetchone()[0])


def daily_units(days: int | None = None) -> pd.DataFrame:
    """Product x day table of units sold, with zero for days with no sales."""
    d = run_sql("daily_demand")
    d["order_date"] = pd.to_datetime(d["order_date"])
    end = today() - pd.Timedelta(days=1)
    start = d["order_date"].min() if days is None else end - pd.Timedelta(days=days - 1)
    grid = (d.pivot_table(index="order_date", columns="product_id", values="units", aggfunc="sum")
             .reindex(pd.date_range(start, end, freq="D"), fill_value=0)
             .fillna(0))
    return grid


# ---------------------------------------------------------------------------
# 1. What sells
# ---------------------------------------------------------------------------
def abc() -> pd.DataFrame:
    return run_sql("abc_analysis")


def monthly_sales() -> pd.DataFrame:
    m = run_sql("monthly_sales")
    m["month"] = pd.to_datetime(m["month"])
    return m


# ---------------------------------------------------------------------------
# 2. Idle stock
# ---------------------------------------------------------------------------
def idle_stock() -> pd.DataFrame:
    return run_sql("idle_stock", {"idle_days": cfg.IDLE_DAYS})


# ---------------------------------------------------------------------------
# 3. Reorder alerts
# ---------------------------------------------------------------------------
def reorder_table() -> pd.DataFrame:
    """Reorder status per product. The maths lives in supply.planning_table()."""
    import supply  # imported here to avoid a circular import
    return supply.planning_table()


# ---------------------------------------------------------------------------
# 4. Demand forecast
# ---------------------------------------------------------------------------
def monthly_units() -> pd.DataFrame:
    """Product x month units, full months only (the current month is partial)."""
    grid = daily_units()
    m = grid.resample("MS").sum()
    return m[m.index < today().to_period("M").to_timestamp()]


def seasonal_index(m: pd.DataFrame) -> pd.Series:
    """
    How busy each calendar month is compared with an average month, measured
    on total company units (more stable than per product). Halved towards 1.0
    so one unusual year doesn't swing the forecast too much.
    With less than a year of history every month is treated as average (1.0).
    """
    if len(m) < 12:
        return pd.Series(1.0, index=range(1, 13))
    share = m.div(m.mean().replace(0, np.nan), axis=1)   # each product vs its own average
    by_month = share.mean(axis=1).groupby(m.index.month).mean()
    idx = 0.5 * by_month + 0.5
    return idx.reindex(range(1, 13), fill_value=1.0)


def _forecast_from(history: pd.DataFrame, target: pd.Timestamp, s_idx: pd.Series) -> pd.Series:
    last3 = history.tail(3)
    deseason = last3.div(s_idx.loc[last3.index.month].values, axis=0).mean()
    return (deseason * s_idx.loc[target.month]).clip(lower=0)


def forecast() -> tuple[pd.DataFrame, dict]:
    """
    Forecast = average of the last 3 full months (with seasonality removed)
               x how busy the target month usually is.
    Also backtests the last 6 months: forecast each month using only data
    before it, compare with what actually sold.
    """
    m = monthly_units()
    s_idx = seasonal_index(m)
    target = today().to_period("M").to_timestamp() + pd.offsets.MonthBegin(1)
    fc = _forecast_from(m, target, s_idx)

    # backtest: the last 6 months, or fewer if the history is short
    # (each test month needs at least 3 earlier months)
    n_test = max(0, min(6, len(m) - 3))
    errs, naive_errs, actual_total = 0.0, 0.0, 0.0
    for i in range(len(m) - n_test, len(m)):
        hist, actual = m.iloc[:i], m.iloc[i]
        pred = _forecast_from(hist, m.index[i], s_idx)
        errs += (pred - actual).abs().sum()
        naive_errs += (hist.iloc[-1] - actual).abs().sum()
        actual_total += actual.sum()
    ok = actual_total > 0
    accuracy = {
        "wape": errs / actual_total if ok else None,             # weighted absolute % error
        "naive_wape": naive_errs / actual_total if ok else None,  # "same as last month"
        "months_tested": n_test,
    }

    names = run_sql("stock_position").set_index("product_id")
    out = pd.DataFrame({
        "product_id": fc.index,
        "product_name": names.loc[fc.index, "product_name"].values,
        "last_3_months_avg": m.tail(3).mean().round(1).values,
        "forecast_units": fc.round(0).astype(int).values,
        "on_hand_units": names.loc[fc.index, "on_hand_units"].values,
    })
    out["target_month"] = target.strftime("%b %Y")
    return out.sort_values("forecast_units", ascending=False), accuracy


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    a = abc()
    print("\nABC (last 12 months)")
    print(a.groupby("abc_class").agg(products=("product_id", "count"),
                                     revenue=("revenue_inr", "sum")))
    total = a.revenue_inr.sum()
    print(f"Revenue last 12 months: {cfg.money(total)}")

    i = idle_stock()
    print(f"\nIdle stock (no sale in {cfg.IDLE_DAYS}+ days): {len(i)} products, "
          f"{cfg.money(i.stock_value_inr.sum())}")
    print(i[["product_name", "on_hand_units", "stock_value_inr", "days_since_last_sale"]])

    r = reorder_table()
    print("\nReorder status:", r.status.value_counts().to_dict())
    print(r[r.status == "Order now"][["product_name", "on_hand_units", "reorder_point",
                                      "days_of_cover", "suggested_order_units"]])
    over = r[r.status == "Overstock"]
    print(f"Overstock value: {cfg.money(over.stock_value_inr.sum())}")

    f, acc = forecast()
    print(f"\nForecast for {f.target_month.iloc[0]}")
    print(f.head(10))
    print(f"Backtest error (WAPE): {acc['wape']:.1%} vs naive {acc['naive_wape']:.1%}")
