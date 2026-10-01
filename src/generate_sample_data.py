"""
Make a realistic but MADE-UP dataset for a small magnetic-inspection-tools maker.

Why: real company data wasn't available, so the project runs on a simulated
company modeled on a small Indian maker of magnetic inspection tools. If real
files are ever available, they go in data/raw/ with the same column names and
every other script works on them unchanged.

Run:  python src/generate_sample_data.py
"""
import numpy as np
import pandas as pd

from config import SAMPLE_DIR

rng = np.random.default_rng(42)          # fixed seed = same data every run
START = pd.Timestamp("2024-10-01")
END = pd.Timestamp("2026-09-22")        # last full day of sales
TODAY = pd.Timestamp("2026-09-23")

# ---------------------------------------------------------------------------
# Products. base = average units sold per month; pattern controls behaviour.
# ---------------------------------------------------------------------------
PRODUCTS = [
    # id, name, category, cost, price, base/mo, pattern, supplier, lead days
    ("P01", "AC Electromagnetic Yoke", "Yokes", 14500, 22500, 22, "steady", "S1", 21),
    ("P02", "AC/DC Electromagnetic Yoke", "Yokes", 19800, 31000, 14, "growing", "S1", 21),
    ("P03", "Permanent Magnet Yoke", "Yokes", 6200, 9800, 18, "steady", "S1", 14),
    ("P04", "Battery-Operated Yoke", "Yokes", 24500, 38500, 4, "growing", "S1", 30),
    ("P05", "Red Magnetic Powder 1 kg", "Consumables", 520, 950, 140, "steady", "S2", 10),
    ("P06", "Black Magnetic Powder 1 kg", "Consumables", 480, 900, 120, "steady", "S2", 10),
    ("P07", "Fluorescent Magnetic Ink 5 L", "Consumables", 3100, 5200, 45, "growing", "S2", 15),
    ("P08", "Black Magnetic Ink Aerosol", "Consumables", 210, 390, 420, "steady", "S3", 7),
    ("P09", "White Contrast Paint Aerosol", "Consumables", 190, 350, 460, "steady", "S3", 7),
    ("P10", "Cleaner Aerosol", "Consumables", 150, 280, 380, "steady", "S3", 7),
    ("P11", "Pie Field Indicator", "Test Pieces", 900, 1650, 16, "steady", "S4", 20),
    ("P12", "Field Indicator Shims (set)", "Test Pieces", 2800, 4700, 9, "steady", "S4", 25),
    ("P13", "Ring Test Block", "Test Pieces", 4200, 7200, 5, "declining", "S4", 30),
    ("P14", "Yoke Lift Test Weight 4.5 kg", "Test Pieces", 1300, 2400, 10, "steady", "S4", 20),
    ("P15", "UV-A Inspection Lamp", "Lighting & Meters", 16500, 26500, 7, "growing", "S5", 35),
    ("P16", "UV Light Meter", "Lighting & Meters", 21000, 33000, 3, "steady", "S5", 40),
    ("P17", "Gauss Meter", "Lighting & Meters", 18000, 29000, 3, "steady", "S5", 40),
    ("P18", "Settling Test Centrifuge Tube", "Accessories", 350, 650, 30, "steady", "S4", 14),
    ("P19", "Yoke Extension Legs (pair)", "Accessories", 1100, 2100, 8, "declining", "S1", 14),
    ("P20", "Demagnetizer Coil", "Equipment", 38000, 58000, 1.2, "steady", "S1", 45),
    ("P21", "Prod Set with Cables", "Accessories", 7800, 12500, 0.8, "dead", "S1", 30),
    ("P22", "Old-Model Yoke Carry Case", "Accessories", 900, 1500, 3, "dead", "S1", 14),
    ("P23", "Magnetic Suspension Concentrate", "Consumables", 2600, 4300, 6, "dead", "S2", 15),
    ("P24", "Wet Horizontal MPI Bench", "Equipment", 285000, 420000, 0.35, "steady", "S1", 60),
]
products = pd.DataFrame(PRODUCTS, columns=[
    "product_id", "product_name", "category", "unit_cost_inr", "unit_price_inr",
    "base_monthly_units", "pattern", "supplier_id", "lead_time_days"])

suppliers = pd.DataFrame([
    ("S1", "Supplier A - Castings & Coils (Pune)", "Pune"),
    ("S2", "Supplier B - Powders & Inks (Vapi)", "Gujarat"),
    ("S3", "Supplier C - Aerosol Filling (Thane)", "Maharashtra"),
    ("S4", "Supplier D - Precision Machining (Chakan)", "Pune"),
    ("S5", "Supplier E - Instruments Importer (Mumbai)", "Maharashtra"),
], columns=["supplier_id", "supplier_name", "location"])

customers = pd.DataFrame({
    "customer_id": [f"C{i:02d}" for i in range(1, 41)],
    "customer_name": [f"Customer {i:02d}" for i in range(1, 41)],
    "segment": rng.choice(
        ["Automotive", "Steel & Forging", "Oil & Gas", "Railways", "NDT Service Co.",
         "Aerospace", "Dealer"], size=40,
        p=[0.25, 0.2, 0.12, 0.08, 0.15, 0.05, 0.15]),
})
# A few big buyers place most orders (like real B2B)
cust_weights = rng.pareto(1.3, 40) + 0.2
cust_weights /= cust_weights.sum()

# ---------------------------------------------------------------------------
# Monthly demand shape
# ---------------------------------------------------------------------------
# India: March rush (financial-year-end budgets), Diwali slowdown (Oct/Nov)
SEASON = {1: 1.0, 2: 1.1, 3: 1.45, 4: 0.8, 5: 0.9, 6: 0.95, 7: 1.0,
          8: 1.0, 9: 1.05, 10: 0.85, 11: 0.75, 12: 1.0}


def trend_factor(pattern: str, month_index: int, n_months: int) -> float:
    t = month_index / max(n_months - 1, 1)
    if pattern == "growing":
        return 0.75 + 0.6 * t
    if pattern == "declining":
        return 1.3 - 0.7 * t
    return 1.0


months = pd.date_range(START, END, freq="MS")
lines = []
invoice_no = 1000
for mi, m in enumerate(months):
    month_end = min(m + pd.offsets.MonthEnd(0), END)
    days = pd.date_range(m, month_end, freq="D")
    workdays = days[days.dayofweek < 6]  # Mon-Sat
    for p in products.itertuples():
        if p.pattern == "dead" and m >= pd.Timestamp("2025-12-01"):
            continue  # stopped selling ~10 months ago
        mean = p.base_monthly_units * SEASON[m.month] * trend_factor(p.pattern, mi, len(months))
        mean *= len(days) / m.days_in_month  # partial last month
        units = rng.poisson(mean)
        while units > 0:
            # one order line: bigger lots for cheap consumables
            lot = max(1, int(rng.gamma(2.0, max(mean / 8, 0.6))))
            lot = min(lot, units)
            lines.append({
                "order_date": rng.choice(workdays),
                "customer_id": rng.choice(customers.customer_id, p=cust_weights),
                "product_id": p.product_id,
                "quantity": lot,
                # small negotiated discounts
                "unit_price_inr": round(p.unit_price_inr * rng.uniform(0.9, 1.0), -1),
            })
            units -= lot

sales = pd.DataFrame(lines).sort_values("order_date").reset_index(drop=True)
sales["invoice_no"] = [f"INV-{1000 + i}" for i in range(len(sales))]
sales["order_date"] = pd.to_datetime(sales["order_date"]).dt.date
sales = sales[["invoice_no", "order_date", "customer_id", "product_id",
               "quantity", "unit_price_inr"]]

# ---------------------------------------------------------------------------
# Current stock: most items sensible, some low, dead items overstocked
# ---------------------------------------------------------------------------
recent = sales[pd.to_datetime(sales.order_date) >= TODAY - pd.Timedelta(days=90)]
daily = recent.groupby("product_id").quantity.sum() / 90
stock_rows = []
for p in products.itertuples():
    d = daily.get(p.product_id, 0)
    if p.pattern == "dead":
        on_hand = int(rng.integers(8, 40)) if p.unit_cost_inr < 10000 else int(rng.integers(6, 12))
    else:
        # stock as a multiple of the lead time: some about to run out, most fine,
        # a couple far too high
        factor = rng.choice([0.5, 0.9, 2.2, 2.8, 3.3, 4.0, 9.0],
                            p=[0.12, 0.1, 0.2, 0.2, 0.15, 0.13, 0.1])
        on_hand = max(0, int(round(d * p.lead_time_days * factor)))
        if p.product_id == "P24":
            on_hand = 1
    stock_rows.append({"product_id": p.product_id, "on_hand_units": on_hand,
                       "as_of_date": TODAY.date()})
stock = pd.DataFrame(stock_rows)

# ---------------------------------------------------------------------------
# Purchases / production receipts (what came into stock)
# ---------------------------------------------------------------------------
# Each supplier has its own reliability: (average delay vs promised, spread)
SUPPLIER_RELIABILITY = {
    "S1": (1.00, 0.15),   # local castings: usually on time
    "S2": (1.05, 0.12),   # powders & inks
    "S3": (1.00, 0.05),   # aerosol filler: very reliable
    "S4": (1.15, 0.25),   # machining shop: often late
    "S5": (1.25, 0.35),   # importer: late and unpredictable (customs, shipping)
}
purch = []
for p in products.itertuples():
    sold = sales.loc[sales.product_id == p.product_id, "quantity"].sum()
    n_orders = max(1, int(sold / max(p.base_monthly_units, 1) / 1.5))
    dates = sorted(rng.choice(pd.date_range(START - pd.Timedelta(days=30), END), n_orders))
    per = max(1, int(round(sold / n_orders)))
    bias, spread = SUPPLIER_RELIABILITY[p.supplier_id]
    for d in dates:
        actual_lt = max(2, int(round(p.lead_time_days * bias * rng.lognormal(0, spread))))
        receipt = pd.Timestamp(d)
        purch.append({"order_date": (receipt - pd.Timedelta(days=actual_lt)).date(),
                      "receipt_date": receipt.date(), "product_id": p.product_id,
                      "supplier_id": p.supplier_id, "quantity": per,
                      "unit_cost_inr": round(p.unit_cost_inr * rng.uniform(0.95, 1.05), -1)})
purchases = pd.DataFrame(purch).sort_values("receipt_date").reset_index(drop=True)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
SAMPLE_DIR.mkdir(exist_ok=True)
products.drop(columns=["base_monthly_units", "pattern"]).to_csv(SAMPLE_DIR / "products.csv", index=False)
suppliers.to_csv(SAMPLE_DIR / "suppliers.csv", index=False)
customers.to_csv(SAMPLE_DIR / "customers.csv", index=False)
sales.to_csv(SAMPLE_DIR / "sales.csv", index=False)
stock.to_csv(SAMPLE_DIR / "stock.csv", index=False)
purchases.to_csv(SAMPLE_DIR / "purchases.csv", index=False)

print(f"products {len(products)}, customers {len(customers)}, "
      f"sales lines {len(sales)}, purchases {len(purchases)}")
print(f"Saved to {SAMPLE_DIR}")
