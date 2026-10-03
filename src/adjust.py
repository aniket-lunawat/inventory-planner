"""
Planner demand adjustments: things the owner knows that the sales history doesn't.

  "Product X +30% in Nov 2026, trade show"      "All products -20% in Oct 2026, Diwali shutdown"

An adjustment is (product_id or ALL, percent change, first month, last month, reason).
It changes:
  - the forecast for next month, when next month falls inside it
  - reorder planning, when it falls inside a product's reorder window: from today until a
    new order would arrive (the actual lead time) plus one more month of selling.
Adjustments multiply: +30% and -10% together give 1.3 x 0.9 = 1.17.
"""
import pandas as pd

ALL = "(All products)"


def _months(start: str, end: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    a = pd.Timestamp(start).to_period("M").to_timestamp()
    b = pd.Timestamp(end).to_period("M").to_timestamp() + pd.offsets.MonthEnd(0)
    return (a, b) if a <= b else (b.to_period("M").to_timestamp(), a + pd.offsets.MonthEnd(0))


def multiplier(adjustments, product_id: str, window_start, window_end) -> float:
    """Combined demand multiplier for one product over a date window."""
    m = 1.0
    for pid, pct, start, end, *_ in adjustments or ():
        if pid not in (ALL, product_id) or pct is None:
            continue
        a, b = _months(start, end)
        if a <= pd.Timestamp(window_end) and b >= pd.Timestamp(window_start):
            m *= 1 + float(pct) / 100
    return max(m, 0.0)


def clean(rows) -> tuple:
    """Rows from the dashboard editor -> a tuple the cache can use. Incomplete rows are skipped."""
    out = []
    for r in rows:
        ok = lambda v: v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip() != ""  # noqa: E731
        pid, pct, start, end = r.get("product"), r.get("change_pct"), r.get("from_month"), r.get("to_month")
        if not (ok(pid) and ok(pct) and ok(start)):
            continue
        end = end if ok(end) else start
        pid = str(pid) if pid == ALL else str(pid).split(" · ")[0]     # "P01 · Yoke" -> "P01"
        out.append((pid, float(pct), str(start), str(end), str(r.get("reason") or "") if ok(r.get("reason")) else ""))
    return tuple(out)
