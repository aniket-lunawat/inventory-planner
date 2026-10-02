"""
Make a second, different made-up company to try the upload feature with:
a small bicycle parts distributor. Saved as sample_data/example_upload.xlsx.

Prices are set in rupees, then saved in US dollars (at config.INR_PER_USD),
because the dashboard's upload defaults to dollars.

It is deliberately a little messy (a few returns, a typo'd product code, no
customers sheet) so the upload checks have something to report.

Run:  python src/make_example_upload.py
"""
import numpy as np
import pandas as pd

from config import INR_PER_USD, SAMPLE_DIR

rng = np.random.default_rng(7)
START, TODAY = pd.Timestamp("2025-04-01"), pd.Timestamp("2026-09-30")

PRODUCTS = [
    # id, name, category, cost, price, units/month, supplier, promised lead days
    ("BK-101", "Inner Tube 26 inch", "Tyres & Tubes", 95, 180, 900, "V1", 10),
    ("BK-102", "Inner Tube 700c", "Tyres & Tubes", 110, 210, 520, "V1", 10),
    ("BK-110", "MTB Tyre 26 x 2.1", "Tyres & Tubes", 520, 890, 160, "V1", 14),
    ("BK-111", "Road Tyre 700 x 25c", "Tyres & Tubes", 780, 1350, 70, "V3", 45),
    ("BK-201", "Brake Pads (pair)", "Brakes", 60, 140, 640, "V2", 12),
    ("BK-202", "Brake Cable Set", "Brakes", 85, 190, 300, "V2", 12),
    ("BK-210", "Hydraulic Disc Brake Kit", "Brakes", 2400, 3900, 18, "V3", 50),
    ("BK-301", "8-Speed Chain", "Drivetrain", 260, 480, 240, "V2", 15),
    ("BK-302", "Freewheel 7-Speed", "Drivetrain", 340, 620, 110, "V2", 15),
    ("BK-310", "Rear Derailleur", "Drivetrain", 1150, 1950, 35, "V3", 45),
    ("BK-401", "Saddle Comfort", "Accessories", 380, 750, 90, "V1", 20),
    ("BK-402", "LED Light Set", "Accessories", 290, 599, 150, "V3", 40),
    ("BK-403", "Bottle Cage", "Accessories", 70, 160, 120, "V1", 20),
    ("BK-404", "Helmet Adult", "Accessories", 650, 1290, 60, "V3", 40),
    ("BK-405", "Mudguard Set (old model)", "Accessories", 140, 260, 40, "V1", 20),
    ("BK-501", "Hand Pump", "Tools", 210, 420, 80, "V1", 20),
]
SEASON = {1: 0.85, 2: 0.95, 3: 1.1, 4: 1.25, 5: 1.2, 6: 1.0, 7: 0.75, 8: 0.8,
          9: 0.95, 10: 1.25, 11: 1.15, 12: 0.9}   # summer and festive peaks, monsoon dip
RELIABILITY = {"V1": (1.0, 0.10), "V2": (1.1, 0.2), "V3": (1.3, 0.35)}   # V3 = importer

products = pd.DataFrame(PRODUCTS, columns=["product_id", "product_name", "category", "unit_cost_inr",
                                           "unit_price_inr", "monthly", "supplier_id", "lead_time_days"])
suppliers = pd.DataFrame({"supplier_id": ["V1", "V2", "V3"],
                          "supplier_name": ["Ludhiana Cycle Parts", "Brake & Chain Works", "Taiwan Import Agent"],
                          "location": ["Ludhiana", "Ludhiana", "Mumbai"]})

lines = []
for m in pd.date_range(START, TODAY - pd.Timedelta(days=1), freq="MS"):
    days = pd.date_range(m, min(m + pd.offsets.MonthEnd(0), TODAY - pd.Timedelta(days=1)))
    for p in products.itertuples():
        mean = p.monthly * SEASON[m.month] * len(days) / m.days_in_month
        if p.product_id == "BK-405" and m >= pd.Timestamp("2026-02-01"):
            mean = 0                                   # discontinued model
        units = rng.poisson(mean)
        while units > 0:
            lot = min(units, max(1, int(rng.gamma(2.0, max(mean / 10, 0.8)))))
            lines.append({"order_date": pd.Timestamp(rng.choice(days)).date(), "product_id": p.product_id,
                          "quantity": lot, "unit_price_inr": round(p.unit_price_inr * rng.uniform(0.88, 1.0))})
            units -= lot
sales = pd.DataFrame(lines).sort_values("order_date").reset_index(drop=True)
sales.insert(0, "invoice_no", [f"B{5000 + i // 3}" for i in range(len(sales))])   # ~3 lines per invoice
sales["customer_id"] = rng.choice([f"D{i:02d}" for i in range(1, 31)], len(sales))
# a little mess for the checks to catch
sales.loc[rng.choice(len(sales), 6, replace=False), "quantity"] *= -1          # returns
sales.loc[rng.choice(len(sales), 3, replace=False), "product_id"] = "BK-1O1"    # letter O typo

recent = sales[(pd.to_datetime(sales.order_date) >= TODAY - pd.Timedelta(days=90)) & (sales.quantity > 0)]
daily = recent.groupby("product_id").quantity.sum() / 90
factor = dict(zip(products.product_id, rng.choice([0.4, 0.8, 2.0, 2.6, 3.2, 4.0, 10.0], len(products),
                                                  p=[0.15, 0.1, 0.2, 0.2, 0.15, 0.1, 0.1])))
factor["BK-405"] = 0
stock = pd.DataFrame({"product_id": products.product_id,
                      "on_hand_units": [int(daily.get(p.product_id, 0) * p.lead_time_days * factor[p.product_id])
                                        for p in products.itertuples()],
                      "as_of_date": TODAY.date()})
stock.loc[stock.product_id == "BK-405", "on_hand_units"] = 260   # dead stock

purch = []
for p in products.itertuples():
    sold = sales.loc[(sales.product_id == p.product_id) & (sales.quantity > 0), "quantity"].sum()
    n = max(2, int(sold / max(p.monthly, 1) / 1.5))
    bias, spread = RELIABILITY[p.supplier_id]
    for d in sorted(rng.choice(pd.date_range(START, TODAY - pd.Timedelta(days=5)), n)):
        lt = max(2, int(round(p.lead_time_days * bias * rng.lognormal(0, spread))))
        purch.append({"order_date": (pd.Timestamp(d) - pd.Timedelta(days=lt)).date(), "receipt_date": pd.Timestamp(d).date(),
                      "product_id": p.product_id, "supplier_id": p.supplier_id,
                      "quantity": max(1, int(sold / n)), "unit_cost_inr": p.unit_cost_inr})
purchases = pd.DataFrame(purch)

def in_dollars(df: pd.DataFrame) -> pd.DataFrame:
    """Rupee columns to dollars, and drop the '_inr' from their names."""
    out = df.copy()
    for c in [c for c in out if c.endswith("_inr")]:
        out[c] = (out[c] / INR_PER_USD).round(2)
    return out.rename(columns=lambda c: c.replace("_inr", ""))


path = SAMPLE_DIR / "example_upload.xlsx"
with pd.ExcelWriter(path, engine="openpyxl") as xw:
    in_dollars(products.drop(columns="monthly")).to_excel(xw, sheet_name="products", index=False)
    in_dollars(sales).to_excel(xw, sheet_name="sales", index=False)
    stock.to_excel(xw, sheet_name="stock", index=False)
    suppliers.to_excel(xw, sheet_name="suppliers", index=False)
    in_dollars(purchases).to_excel(xw, sheet_name="purchases", index=False)
print(f"{len(products)} products, {len(sales):,} sales lines, {len(purchases)} purchases -> {path}")
