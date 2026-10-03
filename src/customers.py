"""
Who buys: customer concentration, which products each customer drives, and who has gone quiet.

  summary()        one row per customer (SQL: customer_summary.sql) plus a status
  concentration()  how many customers make 80% of sales; top-10 share
  product_mix()    customer x product units and each customer's share of a product (SQL window function)
  at_risk()        products whose demand leans on customers who stopped or slowed
"""
import numpy as np
import pandas as pd

import analysis as an

QUIET_FACTOR = 2.5   # silent for 2.5x their usual gap between orders = "gone quiet"
SLOWING = 0.5        # last 3 months below half of the 3 months before = "slowing"


def summary() -> pd.DataFrame:
    t = an.run_sql("customer_summary")
    if t.empty:
        return t
    today = an.today()
    t["last_order"] = pd.to_datetime(t["last_order"])
    t["first_order"] = pd.to_datetime(t["first_order"])
    t["days_since"] = (today - t["last_order"]).dt.days
    span = (t["last_order"] - t["first_order"]).dt.days
    t["usual_gap"] = np.where(t["orders_all_time"] > 1, span / (t["orders_all_time"] - 1).clip(lower=1), np.nan)
    t = t.fillna({"revenue_12m": 0.0, "orders_12m": 0})

    # "slowing" only counts for customers big enough to matter: above the median in the prior 3 months
    big = t["revenue_prior_3m"] >= t.loc[t["revenue_prior_3m"] > 0, "revenue_prior_3m"].median()

    def status(r):
        regular = r.orders_all_time >= 4
        if regular and r.days_since > max(60, QUIET_FACTOR * (r.usual_gap or 0)):
            return "Gone quiet"
        frequent = (r.usual_gap or 999) <= 45      # orders at least every ~6 weeks, so 3 months is a fair test
        if regular and frequent and big[r.name] and r.revenue_last_3m < SLOWING * r.revenue_prior_3m:
            return "Slowing"
        if r.first_order >= today - pd.Timedelta(days=90):
            return "New"
        return "Active"

    t["status"] = t.apply(status, axis=1)
    total = t["revenue_12m"].sum()
    t["share"] = t["revenue_12m"] / total if total else 0.0
    return t.sort_values("revenue_12m", ascending=False).reset_index(drop=True)


def concentration(s: pd.DataFrame) -> dict:
    if s.empty:
        return {"customers": 0}
    cum = s["share"].cumsum()
    return {"customers": int((s["revenue_12m"] > 0).sum()),
            "top1_share": float(s["share"].iloc[0]),
            "top10_share": float(s["share"].head(10).sum()),
            "n_for_80": int((cum - s["share"] < 0.8).sum())}


def product_mix() -> pd.DataFrame:
    return an.run_sql("customer_product_mix")


def at_risk(s: pd.DataFrame, mix: pd.DataFrame, min_share: float = 0.2) -> pd.DataFrame:
    """Products where customers who went quiet or are slowing took at least `min_share` of demand."""
    risky = s[s["status"].isin(["Gone quiet", "Slowing"])]
    if risky.empty or mix.empty:
        return pd.DataFrame()
    m = mix[mix["customer_id"].isin(risky["customer_id"])]
    g = m.groupby(["product_id", "product_name"]).agg(
        share_at_risk=("share_of_product", "sum"),
        customers=("customer_id", lambda x: ", ".join(s.set_index("customer_id").loc[x, "customer_name"])))
    return g[g["share_at_risk"] >= min_share].sort_values("share_at_risk", ascending=False).reset_index()
