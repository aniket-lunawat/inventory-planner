"""
Build sample_data/example_messy_export.xlsx: the bicycle parts distributor's sales,
exported the way small-business accounting software really exports them.

What makes it messy (each is something the raw import must survive):
  - title lines above the header, and the header repeated at every page break
  - product group lines and "Total for ..." subtotal rows, and a grand TOTAL
  - dates as text (MM/DD/YYYY), a few as real Excel dates, one impossible date
  - money as "$1,250.00", negatives as "(45.00)", quantities like "12 pcs"
  - the same product spelt differently ("Brake Pads (pair)" / " brake pads (PAIR)")
  - Credit Memos and Refunds (returns), Shipping, Discount and Sales Tax lines
  - duplicated lines, blank quantities, and one 5,000-unit order keyed in by mistake
    and cancelled with a credit memo
  - no product codes at all: only names

Run:  python src/make_messy_example.py
"""
import numpy as np
import pandas as pd

from config import SAMPLE_DIR

rng = np.random.default_rng(99)
x = pd.read_excel(SAMPLE_DIR / "example_upload.xlsx", sheet_name=None)
names = x["products"].set_index("product_id")["product_name"]
s = x["sales"].copy()
s = s[s.product_id.isin(names.index)]
s["Product/Service"] = s.product_id.map(names)
s["date"] = pd.to_datetime(s.order_date)

rows = []
for _, r in s.iterrows():
    rows.append({"Date": r.date, "Transaction Type": "Invoice", "Num": r.invoice_no,
                 "Customer": f"Bike Shop {r.customer_id}", "Product/Service": r["Product/Service"],
                 "Qty": r.quantity, "Sales Price": r.unit_price})
df = pd.DataFrame(rows)

# returns as credit memos (negative qty) and refunds
ret = df.sample(40, random_state=1).copy()
ret["Transaction Type"] = rng.choice(["Credit Memo", "Refund Receipt"], len(ret))
ret["Qty"] = -np.maximum(1, (ret["Qty"] * 0.3).round())
ret["Date"] = ret["Date"] + pd.Timedelta(days=9)
# non-product lines
extra = df.sample(150, random_state=2).copy()
extra["Product/Service"] = rng.choice(["Shipping", "Discount", "Sales Tax", "Freight Charges"], len(extra))
extra["Qty"] = np.where(extra["Product/Service"].isin(["Shipping", "Freight Charges"]), 1, np.nan)
extra["Sales Price"] = np.where(extra["Product/Service"] == "Discount", -15.0, 12.5)
# a 5,000-unit order keyed in by mistake, then cancelled
oops = df.iloc[[400]].copy()
oops["Qty"], oops["Num"] = 5000, "B9999"
undo = oops.copy()
undo["Transaction Type"], undo["Qty"], undo["Date"] = "Credit Memo", -5000, undo["Date"] + pd.Timedelta(days=1)
# duplicates
dups = df.sample(25, random_state=3)
df = pd.concat([df, ret, extra, oops, undo, dups], ignore_index=True).sort_values(["Product/Service", "Date"])

# inconsistent product names
mask = rng.random(len(df)) < 0.08
df.loc[mask, "Product/Service"] = " " + df.loc[mask, "Product/Service"].str.lower() + " "
mask = rng.random(len(df)) < 0.04
df.loc[mask, "Product/Service"] = df.loc[mask, "Product/Service"].str.upper()

df["Amount"] = df["Qty"].fillna(1) * df["Sales Price"]


def money(v):
    if pd.isna(v):
        return ""
    return f"({abs(v):,.2f})" if v < 0 else f"${v:,.2f}"


out_rows = []
header = ["Date", "Transaction Type", "Num", "Customer", "Product/Service", "Memo/Description", "Qty",
          "Sales Price", "Amount", "Balance"]
title = [["Sunrise Bike Supply LLC"] + [""] * 9, ["Sales by Product/Service Detail"] + [""] * 9,
         ["April 1, 2025 - September 30, 2026"] + [""] * 9, [""] * 10, header]
out_rows += title
balance = 0.0
for prod, grp in df.groupby(df["Product/Service"].str.strip().str.lower(), sort=True):
    out_rows.append([grp["Product/Service"].iloc[0].strip()] + [""] * 9)          # group heading
    sub = 0.0
    for _, r in grp.iterrows():
        d = r.Date
        if rng.random() < 0.85:
            d = d.strftime("%m/%d/%Y")                                          # text date
        q = r.Qty
        qtxt = "" if pd.isna(q) else (f"{q:g} pcs" if rng.random() < 0.15 else f"{q:g}")
        balance += r.Amount
        sub += r.Amount
        out_rows.append([d, r["Transaction Type"], r.Num, r.Customer, r["Product/Service"], "",
                         qtxt, money(r["Sales Price"]), money(r.Amount), f"{balance:,.2f}"])
        if len(out_rows) % 700 == 0:                                              # page break
            out_rows += [[""] * 10, header]
    out_rows.append([f"Total for {grp['Product/Service'].iloc[0].strip()}"] + [""] * 7 + [money(sub), ""])
out_rows.append(["13/45/2025", "Invoice", "B0000", "Bike Shop D01", "Inner Tube 26 inch", "", "3", "$2.17",
                 "$6.51", ""])                                                     # impossible date
out_rows.append(["TOTAL"] + [""] * 7 + [money(df["Amount"].sum()), ""])
out_rows += [[""] * 10, ["Accrual Basis Wednesday, October 1, 2026 09:14 AM GMT-04:00"] + [""] * 9]

path = SAMPLE_DIR / "example_messy_export.xlsx"
with pd.ExcelWriter(path, engine="openpyxl") as xw:
    pd.DataFrame(out_rows).to_excel(xw, sheet_name="Sales by Product", header=False, index=False)
    xw.book["Sales by Product"].column_dimensions["E"].width = 30
print(f"{len(out_rows):,} rows -> {path}")
