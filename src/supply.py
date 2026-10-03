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

import adjust
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
    return np.sqrt(2 * annual_units * order_cost() / h)


def order_cost() -> float:
    """Cost of one purchase order, in the active data's currency (₹1,500, or about $18)."""
    cur = an.data_currency()
    return cfg.ORDER_COST_INR if cur == "INR" else cfg.ORDER_COST_INR / cfg.INR_PER_USD   # $, £ or €: about 18


def planning_table(z: float = cfg.SERVICE_LEVEL_Z, adjustments=()) -> pd.DataFrame:
    """
    One row per product: demand, lead time, safety stock, reorder point, EOQ and status.
    `adjustments` (see adjust.py) scale demand for products whose reorder window they fall in.
    """
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

    # planner adjustments: scale expected demand (and its spread) inside each product's reorder window
    now = an.today()
    t["demand_adjust"] = [adjust.multiplier(adjustments, pid, now, now + pd.Timedelta(days=lt + 30))
                          for pid, lt in zip(t["product_id"], t["lt_mean"].fillna(0))]
    t["avg_daily_demand"] *= t["demand_adjust"]
    t["std_daily_demand"] *= t["demand_adjust"]
    t["annual_units"] = t["annual_units"] * t["demand_adjust"]

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
# Comparing suppliers on what they really cost
# ---------------------------------------------------------------------------
UNKNOWN_RATIO = (1.15, 0.25)   # a supplier with no history: assume an average one (15% late, +-25%)


def supplier_reliability() -> pd.DataFrame:
    """Per supplier: actual / promised lead time (mean and spread) from past deliveries."""
    r = an.run_sql("supplier_lead_ratio")
    if r.empty:
        return pd.DataFrame(columns=["supplier_name", "ratio_mean", "ratio_sd", "deliveries"])
    g = r.groupby("supplier_id")
    return pd.DataFrame({"supplier_name": g["supplier_name"].first(), "ratio_mean": g["ratio"].mean(),
                         "ratio_sd": g["ratio"].std(ddof=1).fillna(0.1), "deliveries": g.size()})


def option_lead_time(promised: float, reliability: str) -> tuple[float, float, str]:
    """
    Expected lead time (mean, spread) for a quote, and how it was estimated:
      a supplier id -> scale the promise by that supplier's track record
      'on_time'     -> trust the promise (small 5% spread)
      'unknown'     -> assume an average supplier
    """
    rel = supplier_reliability()
    if reliability in rel.index:
        r = rel.loc[reliability]
        return promised * r.ratio_mean, promised * r.ratio_sd, \
            f"{r.supplier_name}'s record: {r.ratio_mean - 1:+.0%} vs promise over {int(r.deliveries)} deliveries"
    if reliability == "on_time":
        return promised, 0.05 * promised, "assumed to deliver on time"
    m, s = UNKNOWN_RATIO
    return promised * m, promised * s, "no history: assumed an average supplier (15% late, ±25%)"


def yearly_cost(d, sd_d, annual_units, unit_cost, lt_mean, lt_sd, z=cfg.SERVICE_LEVEL_Z) -> dict:
    """
    Total yearly cost of buying a product from one supplier:
      purchases      annual units x unit cost
      ordering       orders per year (annual units / EOQ) x cost per order
      cycle stock    average stock between deliveries (EOQ / 2) x holding cost
      safety stock   safety stock x holding cost  <- a late or erratic supplier costs more here
    """
    q = max(float(eoq(max(annual_units, 1), unit_cost)), 1.0)
    ss = float(np.ceil(safety_stock(z, d, sd_d, lt_mean, lt_sd)))
    h = cfg.HOLDING_RATE * unit_cost
    parts = {"purchases": annual_units * unit_cost, "ordering": annual_units / q * order_cost(),
             "cycle_stock": q / 2 * h, "safety_stock_cost": ss * h}
    return {**parts, "total": sum(parts.values()), "safety_stock_units": ss, "eoq": q}


def compare_suppliers(product_id: str, options: list, z=cfg.SERVICE_LEVEL_Z, adjustments=()) -> pd.DataFrame:
    """
    Current supplier vs each option for one product, on total yearly cost.
    options: [{"supplier_name", "unit_cost", "promised_lead_time_days", "reliability"}]
    """
    row = planning_table(z, adjustments).set_index("product_id").loc[product_id]
    d, sd_d, D = row.avg_daily_demand, row.std_daily_demand, row.annual_units
    rows = [{"supplier": f"{row.supplier_name or 'Current supplier'} (current)", "unit_cost": row.unit_cost_inr,
             "promised_days": row.lead_time_days, "expected_days": row.lt_mean, "spread_days": row.lt_std,
             "basis": "this product's own past deliveries" if row.lt_obs >= 3 else "promised lead time (few deliveries)",
             **yearly_cost(d, sd_d, D, row.unit_cost_inr, row.lt_mean, row.lt_std, z)}]
    for o in options:
        lt_m, lt_s, basis = option_lead_time(float(o["promised_lead_time_days"]), str(o.get("reliability", "unknown")))
        rows.append({"supplier": o["supplier_name"], "unit_cost": float(o["unit_cost"]),
                     "promised_days": float(o["promised_lead_time_days"]), "expected_days": lt_m,
                     "spread_days": lt_s, "basis": basis,
                     **yearly_cost(d, sd_d, D, float(o["unit_cost"]), lt_m, lt_s, z)})
    t = pd.DataFrame(rows)
    t["vs_current"] = t["total"] - t["total"].iloc[0]
    t.attrs.update(product=row.product_name, annual_units=D)
    return t


def sample_supplier_options() -> pd.DataFrame:
    """Alternative quotes for the simulated company (only meaningful on the sample data)."""
    path = cfg.SAMPLE_DIR / "supplier_options.csv"
    return pd.read_csv(path, dtype={"product_id": str}) if path.exists() else pd.DataFrame()


def supplier_switch_summary(adjustments=()) -> pd.DataFrame:
    """Every sample quote compared with the current supplier: best choice per product."""
    opts = sample_supplier_options()
    out = []
    for pid, grp in opts.groupby("product_id", sort=False):
        t = compare_suppliers(pid, [{"supplier_name": r.supplier_name, "unit_cost": r.unit_cost_inr,
                                     "promised_lead_time_days": r.promised_lead_time_days,
                                     "reliability": r.reliability} for r in grp.itertuples()],
                              adjustments=adjustments)
        best = t.loc[t["total"].idxmin()]
        alt = t.iloc[1]
        out.append({"product_id": pid, "product": t.attrs["product"], "current": t.iloc[0]["supplier"],
                    "alternative": alt["supplier"], "price_change": alt["unit_cost"] / t.iloc[0]["unit_cost"] - 1,
                    "lead_time_change": alt["expected_days"] - t.iloc[0]["expected_days"],
                    "yearly_saving": -alt["vs_current"],
                    "verdict": "Switch" if best["supplier"] == alt["supplier"] else "Stay",
                    "note": grp.iloc[0]["note"]})
    return pd.DataFrame(out)


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
