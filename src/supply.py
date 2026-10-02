"""
Supply-side analysis: suppliers, safety stock, order quantity, turnover and a
what-if simulation of service levels.

  supplier_scorecard()   promised vs actual lead times, on-time %
  planning_table()       per product: demand, lead time, safety stock,
                         reorder point, EOQ, status (feeds the reorder tab)
  turnover()             inventory turnover and days of inventory
  simulate_service_levels()  a year of daily operations, simulated many times,
                         for several service levels: fill rate vs stock cost

Run on its own to print a summary:  python src/supply.py
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

import analysis as an
import config as cfg


# ---------------------------------------------------------------------------
# Suppliers and lead times
# ---------------------------------------------------------------------------
def supplier_scorecard() -> pd.DataFrame:
    return an.run_sql("supplier_performance", {"grace": cfg.ON_TIME_GRACE_DAYS})


def lead_time_stats() -> pd.DataFrame:
    """Average and spread of ACTUAL lead time per product (not the promised one)."""
    lt = an.run_sql("lead_times")
    g = lt.groupby("product_id")["actual_days"]
    return pd.DataFrame({"lt_mean": g.mean(), "lt_std": g.std(ddof=1).fillna(0), "lt_obs": g.count()})


# ---------------------------------------------------------------------------
# Planning table: safety stock, reorder point, EOQ
# ---------------------------------------------------------------------------
def demand_stats() -> pd.DataFrame:
    """Average daily demand and its spread, measured on weekly totals (B2B orders are lumpy)."""
    grid = an.daily_units(cfg.DEMAND_WINDOW_DAYS)
    weekly = grid.resample("W").sum()
    return pd.DataFrame({
        "avg_daily_demand": grid.mean(),
        "std_daily_demand": weekly.std(ddof=1) / np.sqrt(7),  # weekly spread scaled to one day
    })


def safety_stock(z, d, sd_d, lt, sd_lt):
    """
    Safety stock when BOTH demand and lead time vary:
        SS = z * sqrt( LT * sd_demand^2  +  demand^2 * sd_LT^2 )
    First term: demand swings during a normal lead time.
    Second term: the extra days of demand when the supplier is late.
    """
    return z * np.sqrt(lt * sd_d ** 2 + d ** 2 * sd_lt ** 2)


def eoq(annual_units, unit_cost):
    """
    Economic order quantity: the order size that balances ordering cost
    (fewer, bigger orders) against holding cost (smaller stock).
        EOQ = sqrt( 2 x annual demand x cost per order / yearly holding cost per unit )
    """
    h = cfg.HOLDING_RATE * unit_cost
    return np.sqrt(2 * annual_units * cfg.ORDER_COST_INR / h)


def planning_table(z: float = cfg.SERVICE_LEVEL_Z) -> pd.DataFrame:
    pos = an.run_sql("stock_position")
    t = (pos.merge(demand_stats(), left_on="product_id", right_index=True, how="left")
            .merge(lead_time_stats(), left_on="product_id", right_index=True, how="left")
            .merge(an.run_sql("annual_demand")[["product_id", "annual_units"]], on="product_id"))
    t[["avg_daily_demand", "std_daily_demand"]] = t[["avg_daily_demand", "std_daily_demand"]].fillna(0)
    # products with too few deliveries fall back to the promised lead time
    few = t["lt_obs"].fillna(0) < 3
    t.loc[few, "lt_mean"] = t.loc[few, "lead_time_days"]
    t.loc[few, "lt_std"] = 0.2 * t.loc[few, "lead_time_days"]
    t[["lt_mean", "lt_std"]] = t[["lt_mean", "lt_std"]].astype(float)

    d, sd, lt, slt = t.avg_daily_demand, t.std_daily_demand, t.lt_mean, t.lt_std
    t["safety_stock"] = np.ceil(safety_stock(z, d, sd, lt, slt))
    t["safety_stock_naive"] = np.ceil(z * sd * np.sqrt(t.lead_time_days))  # promised LT, no LT spread
    t["reorder_point"] = np.ceil(d * lt + t["safety_stock"])
    t["eoq"] = np.ceil(eoq(t["annual_units"].clip(lower=1), t["unit_cost_inr"])).astype(int)
    t["days_of_cover"] = np.where(d > 0, t["on_hand_units"] / d.replace(0, np.nan), np.inf)
    t["stock_value_inr"] = t["on_hand_units"] * t["unit_cost_inr"]

    def status(r):
        if r.avg_daily_demand == 0:
            return "Not selling"
        if r.avg_daily_demand * 30 < cfg.MAKE_TO_ORDER_BELOW:
            return "Make to order"
        if r.on_hand_units <= r.reorder_point:
            return "Order now"
        if r.days_of_cover > cfg.OVERSTOCK_MONTHS * 30:
            return "Overstock"
        return "OK"

    t["status"] = t.apply(status, axis=1)
    # order the EOQ, but at least enough to climb back above the reorder point
    need = np.maximum(t["eoq"], t["reorder_point"] - t["on_hand_units"] + 1)
    t["suggested_order_units"] = np.where(t["status"] == "Order now", need, 0).astype(int)
    t["suggested_order_value_inr"] = t["suggested_order_units"] * t["unit_cost_inr"]
    order = {"Order now": 0, "Overstock": 1, "Not selling": 2, "Make to order": 3, "OK": 4}
    return t.sort_values(["status", "days_of_cover"],
                         key=lambda s: s.map(order) if s.name == "status" else s)


# ---------------------------------------------------------------------------
# Turnover
# ---------------------------------------------------------------------------
def turnover() -> pd.DataFrame:
    """
    Inventory turnover = cost of goods sold in 12 months / value of stock on hand.
    Days of inventory  = 365 / turnover.
    Uses today's stock as the average stock (no stock history in the data).
    """
    a = an.run_sql("annual_demand")
    pos = an.run_sql("stock_position")[["product_id", "category", "on_hand_units"]]
    t = a.merge(pos, on="product_id")
    t["stock_value_inr"] = t["on_hand_units"] * t["unit_cost_inr"]
    by = t.groupby("category")[["annual_cogs_inr", "stock_value_inr"]].sum()
    by.loc["All products"] = by.sum()
    by["turnover"] = by["annual_cogs_inr"] / by["stock_value_inr"].replace(0, np.nan)
    by["days_of_inventory"] = 365 / by["turnover"]
    return by.reset_index()


# ---------------------------------------------------------------------------
# What-if: service level simulation
# ---------------------------------------------------------------------------
def _simulate_product(daily_hist, lt_samples, s, q, start_stock, days, runs, rng):
    """
    Simulate one product under the policy "when stock on hand + on order <= s, order q".
    Demand each day is drawn from this product's real daily history (bootstrap);
    each order's lead time is drawn from its supplier's real past deliveries.
    Unmet demand is lost. Returns fill rate, stockout days and average stock (units).
    """
    on_hand = np.full(runs, float(start_stock))
    max_open = 8
    arrive = np.full((runs, max_open), np.inf)          # arrival day of each open order
    served = np.zeros(runs)
    demanded = np.zeros(runs)
    stockout_days = np.zeros(runs)
    stock_sum = np.zeros(runs)
    demand = rng.choice(daily_hist, size=(days, runs))
    for t in range(days):
        hit = arrive == t
        on_hand += q * hit.sum(axis=1)
        arrive[hit] = np.inf
        d = demand[t]
        got = np.minimum(on_hand, d)
        served += got
        demanded += d
        stockout_days += d > on_hand
        on_hand -= got
        stock_sum += on_hand
        position = on_hand + q * np.isfinite(arrive).sum(axis=1)
        need = position <= s
        if need.any():
            slot = np.argmax(~np.isfinite(arrive), axis=1)   # first free slot
            lts = rng.choice(lt_samples, size=runs)
            rows = np.where(need & ~np.isfinite(arrive).all(axis=1))[0]
            arrive[rows, slot[rows]] = t + np.maximum(1, np.round(lts[rows]))
    fill = np.where(demanded > 0, served / np.maximum(demanded, 1e-9), 1.0)
    return fill.mean(), stockout_days.mean(), (stock_sum / days).mean()


def simulate_service_levels(levels=(0.90, 0.95, 0.98, 0.99), days=365, runs=300, seed=7):
    """
    For each target service level, set safety stock and reorder points, then run a
    year of daily operations `runs` times. Report fill rate, stockout days,
    average stock value and its yearly holding cost, totalled over stocked products.
    """
    rng = np.random.default_rng(seed)
    base = planning_table()
    stocked = base[~base["status"].isin(["Not selling", "Make to order"])]
    hist = an.daily_units().tail(365)   # up to a year of real daily demand
    lt_raw = an.run_sql("lead_times")
    rows = []
    for level in levels:
        z = norm.ppf(level)
        plan = planning_table(z).set_index("product_id").loc[stocked["product_id"]]
        tot = {"served_w": 0.0, "demand_w": 0.0, "stock_value": 0.0, "stockout_days": 0.0}
        for pid, r in plan.iterrows():
            lts = lt_raw.loc[lt_raw.product_id == pid, "actual_days"].to_numpy()
            if len(lts) < 3:
                lts = np.array([r.lt_mean])
            fill, so_days, avg_units = _simulate_product(
                hist[pid].to_numpy(), lts, r.reorder_point, max(r.eoq, 1),
                start_stock=r.reorder_point + r.eoq, days=days, runs=runs, rng=rng)
            yearly_units = hist[pid].sum()
            tot["served_w"] += fill * yearly_units * r.unit_cost_inr
            tot["demand_w"] += yearly_units * r.unit_cost_inr
            tot["stock_value"] += avg_units * r.unit_cost_inr
            tot["stockout_days"] += so_days
        rows.append({
            "target_service_level": level,
            "z": round(z, 2),
            "fill_rate": tot["served_w"] / max(tot["demand_w"], 1e-9),  # value-weighted share of demand met
            "stockout_days_per_product": tot["stockout_days"] / max(len(plan), 1),
            "avg_stock_value_inr": tot["stock_value"],
            "yearly_holding_cost_inr": tot["stock_value"] * cfg.HOLDING_RATE,
            "safety_stock_value_inr": (plan["safety_stock"] * plan["unit_cost_inr"]).sum(),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pd.set_option("display.width", 160)
    print("\nSupplier scorecard")
    print(supplier_scorecard().drop(columns="supplier_id").to_string(index=False))

    p = planning_table()
    print("\nStatus:", p.status.value_counts().to_dict())
    cols = ["product_name", "lead_time_days", "lt_mean", "lt_std", "safety_stock_naive",
            "safety_stock", "reorder_point", "eoq", "on_hand_units", "status"]
    print(p[cols].round(1).to_string(index=False))

    print("\nTurnover")
    print(turnover().round(2).to_string(index=False))

    print("\nService level what-if (simulated year, 300 runs)")
    print(simulate_service_levels().round(3).to_string(index=False))
