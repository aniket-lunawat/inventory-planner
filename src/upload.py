"""
Let anyone run the planner on their own data.

  template_xlsx()   a blank Excel template with instructions (bytes, for a download button)
  read_upload()     reads an uploaded Excel file (one sheet per table) or a set of CSVs
  validate()        checks and cleans the tables; returns plain-English errors and warnings
  build()           writes a private SQLite database for this upload and returns its path

Rules of thumb:
  - Only 3 sheets are required: products, sales, stock.
  - suppliers, purchases and customers are optional. Without purchases, the tool
    uses each product's promised lead time (no supplier scorecard).
  - Problems that make the analysis impossible are ERRORS (nothing is built).
    Problems that only affect some rows are WARNINGS (those rows are skipped).
"""
import hashlib
import io
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from load_db import TABLES, build_db

# ---------------------------------------------------------------------------
# What each sheet needs
# ---------------------------------------------------------------------------
# table: (required columns, optional columns)
COLUMNS = {
    "products": (["product_id", "product_name", "unit_cost_inr", "lead_time_days"],
                 ["category", "unit_price_inr", "supplier_id"]),
    "sales": (["order_date", "product_id", "quantity", "unit_price_inr"],
              ["invoice_no", "customer_id"]),
    "stock": (["product_id", "on_hand_units"], ["as_of_date"]),
    "suppliers": (["supplier_id", "supplier_name"], ["location"]),
    "purchases": (["order_date", "receipt_date", "product_id", "quantity"],
                  ["supplier_id", "unit_cost_inr"]),
    "customers": (["customer_id", "customer_name"], ["segment"]),
}
REQUIRED_SHEETS = ["products", "sales", "stock"]

# What each column means (used in the template's "Read me" sheet)
HELP = {
    "products": {
        "product_id": ("Your code for the item (SKU)", "P01"),
        "product_name": ("Name of the item", "AC Electromagnetic Yoke"),
        "unit_cost_inr": ("What one unit costs you to make or buy", 175.00),
        "lead_time_days": ("Days the supplier promises from order to delivery", 21),
        "category": ("Product group (optional)", "Yokes"),
        "unit_price_inr": ("List selling price (optional; else taken from sales)", 271.00),
        "supplier_id": ("Who supplies it (optional; matches the suppliers sheet)", "S1"),
    },
    "sales": {
        "order_date": ("Date of the sale (YYYY-MM-DD)", "2026-08-14"),
        "product_id": ("Item sold (matches the products sheet)", "P01"),
        "quantity": ("Units sold on that line", 5),
        "unit_price_inr": ("Price per unit on that line", 253.00),
        "invoice_no": ("Invoice number (optional)", "INV-1001"),
        "customer_id": ("Customer (optional)", "C01"),
    },
    "stock": {
        "product_id": ("Item (one row per product)", "P01"),
        "on_hand_units": ("Units in stock right now", 145),
        "as_of_date": ("Date of the stock count (optional; else the day after the last sale)", "2026-09-23"),
    },
    "suppliers": {
        "supplier_id": ("Supplier code", "S1"),
        "supplier_name": ("Supplier name", "Castings & Coils"),
        "location": ("City (optional)", "Pune"),
    },
    "purchases": {
        "order_date": ("Date you placed the purchase order", "2026-07-01"),
        "receipt_date": ("Date the goods arrived", "2026-07-24"),
        "product_id": ("Item received", "P01"),
        "quantity": ("Units received", 20),
        "supplier_id": ("Supplier (optional; else from products)", "S1"),
        "unit_cost_inr": ("Cost per unit (optional)", 171.00),
    },
    "customers": {
        "customer_id": ("Customer code", "C01"),
        "customer_name": ("Customer name", "Customer 01"),
        "segment": ("Industry or type (optional)", "Automotive"),
    },
}

# Common header names people use, mapped to ours (after lower-casing)
ALIASES = {
    "sku": "product_id", "item_code": "product_id", "item_id": "product_id", "product_code": "product_id",
    "item_name": "product_name", "description": "product_name", "name": "product_name",
    "unit_cost": "unit_cost_inr", "cost": "unit_cost_inr",
    "unit_price": "unit_price_inr", "price": "unit_price_inr", "selling_price": "unit_price_inr",
    "lead_time": "lead_time_days", "lead_days": "lead_time_days",
    "qty": "quantity", "units": "quantity",
    "on_hand": "on_hand_units", "stock": "on_hand_units", "stock_units": "on_hand_units", "closing_stock": "on_hand_units",
    "invoice": "invoice_no", "invoice_number": "invoice_no",
    "supplier": "supplier_id", "vendor_id": "supplier_id", "vendor_name": "supplier_name",
    "customer": "customer_id",
}
DATE_ALIASES = {"sales": {"date": "order_date", "invoice_date": "order_date", "sale_date": "order_date"},
                "stock": {"date": "as_of_date", "stock_date": "as_of_date"},
                "purchases": {"po_date": "order_date", "received_date": "receipt_date", "grn_date": "receipt_date"}}

MAX_PRODUCTS = 1000
MAX_SALES_ROWS = 300_000
MIN_HISTORY_DAYS = 90
DEFAULT_LEAD_TIME = 14


def _shown(col: str) -> str:
    """Column name as people see it: the database adds '_inr', but uploads can be in any currency."""
    return col.replace("_inr", "")


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------
def template_xlsx() -> bytes:
    """Blank template: a 'Read me' sheet, then one sheet per table with one example row."""
    from openpyxl.styles import Font, PatternFill

    readme = []
    for table in ["products", "sales", "stock", "suppliers", "purchases", "customers"]:
        req, opt = COLUMNS[table]
        need = "Required" if table in REQUIRED_SHEETS else "Optional"
        for col in req + opt:
            readme.append({"Sheet": table, "Sheet needed?": need,
                           "Column": _shown(col), "Column needed?": "Required" if col in req else "Optional",
                           "What to put in it": HELP[table][col][0], "Example": HELP[table][col][1]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        intro = pd.DataFrame({"Inventory Planner: data template": [
            "1. Fill in the sheets products, sales and stock (required). suppliers, purchases and customers are optional.",
            "2. Keep the column names in row 1 exactly as they are. Delete the example row in each sheet.",
            "3. Dates as YYYY-MM-DD. At least 3 months of sales; 12 months or more gives a seasonal forecast.",
            "4. Every product_id in sales, stock and purchases must also be in products.",
            "5. Amounts in US dollars or Indian rupees (pick which in the sidebar when you upload). Use one currency throughout.",
            "6. Save, then upload this file in the dashboard sidebar. Nothing is stored after you close the page.",
            "",
            "Column guide:"]})
        intro.to_excel(xw, sheet_name="Read me", index=False)
        pd.DataFrame(readme).to_excel(xw, sheet_name="Read me", index=False, startrow=len(intro) + 2)
        for table in ["products", "sales", "stock", "suppliers", "purchases", "customers"]:
            req, opt = COLUMNS[table]
            pd.DataFrame([{_shown(c): HELP[table][c][1] for c in req + opt}]).to_excel(xw, sheet_name=table, index=False)

        wb = xw.book
        bold, req_fill = Font(bold=True), PatternFill("solid", fgColor="DCE9F9")
        ws = wb["Read me"]
        ws["A1"].font = Font(bold=True, size=13)
        for cell in ws[len(intro) + 3]:
            cell.font = bold
        for col, width in zip("ABCDEF", [12, 14, 16, 16, 62, 22]):
            ws.column_dimensions[col].width = width
        for table in COLUMNS:
            ws = wb[table]
            req = COLUMNS[table][0]
            for cell in ws[1]:
                cell.font = bold
                if cell.value in [_shown(c) for c in req]:
                    cell.fill = req_fill
                ws.column_dimensions[cell.column_letter].width = max(14, len(str(cell.value)) + 4)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def _norm(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def read_upload(files) -> tuple[dict, bytes]:
    """
    `files` = list of (filename, bytes). Accepts one Excel workbook (one sheet per
    table) and/or CSV files named after the tables (products.csv, sales.csv ...).
    Returns ({table: DataFrame}, all bytes together for a fingerprint).
    """
    frames, blob = {}, b""
    for name, data in files:
        blob += name.encode() + data
        suffix = Path(name).suffix.lower()
        if suffix in (".xlsx", ".xlsm", ".xls"):
            for sheet, df in pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object).items():
                frames[_norm(sheet)] = df
        elif suffix == ".csv":
            frames[_norm(Path(name).stem)] = pd.read_csv(io.BytesIO(data), dtype=object)
    return frames, blob


# ---------------------------------------------------------------------------
# Checking and cleaning
# ---------------------------------------------------------------------------
def _ids(s: pd.Series) -> pd.Series:
    """IDs as clean text. Excel turns 1001 into 1001.0; undo that so sheets match."""
    def one(v):
        if pd.isna(v):
            return np.nan
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        return str(v).strip()
    return s.map(one)


def _num(s: pd.Series) -> pd.Series:
    if s.dtype == object:
        s = s.astype(str).str.replace(",", "").str.replace("₹", "").str.replace("$", "").str.strip()
    return pd.to_numeric(s, errors="coerce")


def _date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed").dt.normalize()


def validate(raw: dict) -> tuple[dict | None, list, list, dict]:
    """
    Returns (tables, errors, warnings, summary). `tables` is None when there are errors.
    """
    errors, warnings = [], []

    # 1. Sheets and columns -------------------------------------------------
    t = {}
    for table, (req, opt) in COLUMNS.items():
        if table not in raw:
            if table in REQUIRED_SHEETS:
                errors.append(f"Missing the **{table}** sheet. Found: {', '.join(raw) or 'nothing'}.")
            continue
        df = raw[table].copy()
        df.columns = [_norm(c) for c in df.columns]
        alias = {**ALIASES, **DATE_ALIASES.get(table, {})}
        df = df.rename(columns={c: alias[c] for c in df.columns if c in alias and alias[c] not in df.columns})
        df = df.dropna(how="all")
        missing = [c for c in req if c not in df.columns]
        if missing:
            errors.append(f"The **{table}** sheet is missing column(s): {', '.join(map(_shown, missing))}. "
                          f"It has: {', '.join(map(str, df.columns))}.")
            continue
        t[table] = df[[c for c in req + opt if c in df.columns]].copy()
    if errors:
        return None, errors, warnings, {}

    # 2. Products -------------------------------------------------------------
    p = t["products"]
    p["product_id"] = _ids(p["product_id"])
    p = p.dropna(subset=["product_id"])
    dup = p["product_id"].duplicated()
    if dup.any():
        warnings.append(f"{dup.sum()} duplicate product_id row(s) in products; kept the first of each.")
        p = p[~dup]
    p["product_name"] = p["product_name"].fillna(p["product_id"]).astype(str).str.strip()
    p["unit_cost_inr"] = _num(p["unit_cost_inr"])
    p["lead_time_days"] = _num(p["lead_time_days"])
    bad_cost = p["unit_cost_inr"].isna() | (p["unit_cost_inr"] <= 0)
    if bad_cost.any():
        warnings.append(f"{bad_cost.sum()} product(s) have no valid unit cost and were skipped: "
                        f"{', '.join(p.loc[bad_cost, 'product_id'].head(5))}.")
        p = p[~bad_cost]
    bad_lt = p["lead_time_days"].isna() | (p["lead_time_days"] <= 0)
    if bad_lt.any():
        warnings.append(f"{bad_lt.sum()} product(s) have no lead time; assumed {DEFAULT_LEAD_TIME} days.")
        p.loc[bad_lt, "lead_time_days"] = DEFAULT_LEAD_TIME
    p["lead_time_days"] = p["lead_time_days"].round().astype(int)
    p["category"] = (p["category"] if "category" in p else pd.Series(index=p.index, dtype=object)).fillna("Uncategorised")
    p["supplier_id"] = _ids(p["supplier_id"]) if "supplier_id" in p else pd.Series(None, index=p.index, dtype=object)
    if len(p) == 0:
        return None, ["No usable products: every row is missing an id or a cost."], warnings, {}
    if len(p) > MAX_PRODUCTS:
        return None, [f"{len(p):,} products is more than this online tool handles ({MAX_PRODUCTS:,}). "
                      "Upload your top products, or run the project on your own computer."], warnings, {}
    known = set(p["product_id"])

    # 3. Sales ----------------------------------------------------------------
    s = t["sales"]
    if len(s) > MAX_SALES_ROWS:
        return None, [f"{len(s):,} sales rows is more than the online limit ({MAX_SALES_ROWS:,}). "
                      "Upload the last 2 years, or run it on your own computer."], warnings, {}
    s["product_id"] = _ids(s["product_id"])
    s["order_date"] = _date(s["order_date"])
    s["quantity"] = _num(s["quantity"])
    s["unit_price_inr"] = _num(s["unit_price_inr"])
    checks = [
        (s["order_date"].isna(), "have no valid date"),
        (s["quantity"].isna() | (s["quantity"] <= 0), "have a zero, negative or missing quantity (returns are not demand)"),
        (s["unit_price_inr"].isna() | (s["unit_price_inr"] < 0), "have a missing or negative price"),
        (~s["product_id"].isin(known), "are for a product_id that is not in products"),
    ]
    keep = pd.Series(True, index=s.index)
    for bad, why in checks:
        bad = bad & keep
        if bad.any():
            ex = ""
            if "product_id" in why:
                ex = f" (e.g. {', '.join(s.loc[bad, 'product_id'].dropna().astype(str).unique()[:5])})"
            warnings.append(f"{bad.sum():,} sales row(s) {why}{ex}; skipped.")
            keep &= ~bad
    s = s[keep].copy()
    if len(s) == 0:
        return None, ["No usable sales rows after checking. Look at the warnings below."], warnings, {}
    s["quantity"] = s["quantity"].round().astype(int)
    s["invoice_no"] = s["invoice_no"].astype(str) if "invoice_no" in s else [f"L{i}" for i in range(len(s))]
    s["customer_id"] = _ids(s["customer_id"]).fillna("UNKNOWN") if "customer_id" in s else "UNKNOWN"
    span = (s["order_date"].max() - s["order_date"].min()).days
    if span < MIN_HISTORY_DAYS:
        return None, [f"Sales cover only {span} days ({s['order_date'].min():%d %b %Y} to "
                      f"{s['order_date'].max():%d %b %Y}). The tool needs at least 3 months to measure demand."], warnings, {}

    # list price from sales when not given
    avg_price = s.groupby("product_id")["unit_price_inr"].mean()
    lp = _num(p["unit_price_inr"]) if "unit_price_inr" in p else pd.Series(np.nan, index=p.index)
    p["unit_price_inr"] = lp.fillna(p["product_id"].map(avg_price)).fillna(p["unit_cost_inr"])

    # 4. Stock ----------------------------------------------------------------
    st = t["stock"]
    st["product_id"] = _ids(st["product_id"])
    st["on_hand_units"] = _num(st["on_hand_units"])
    if "as_of_date" in st:
        st["as_of_date"] = _date(st["as_of_date"])
    unknown = ~st["product_id"].isin(known)
    if unknown.any():
        warnings.append(f"{unknown.sum()} stock row(s) are for products not in products; skipped.")
        st = st[~unknown]
    st = st.groupby("product_id", as_index=False).agg(
        on_hand_units=("on_hand_units", "sum"),
        **({"as_of_date": ("as_of_date", "max")} if "as_of_date" in st else {}))
    st["on_hand_units"] = st["on_hand_units"].fillna(0).clip(lower=0).round().astype(int)
    no_stock = sorted(known - set(st["product_id"]))
    if no_stock:
        warnings.append(f"{len(no_stock)} product(s) have no stock row; counted as 0 in stock "
                        f"(e.g. {', '.join(no_stock[:5])}).")
        st = pd.concat([st, pd.DataFrame({"product_id": no_stock, "on_hand_units": 0})], ignore_index=True)
    as_of = _date(st["as_of_date"]).max() if "as_of_date" in st else pd.NaT
    if pd.isna(as_of):
        as_of = s["order_date"].max() + pd.Timedelta(days=1)
        warnings.append(f"No stock date given; using the day after the last sale ({as_of:%d %b %Y}).")
    late = s["order_date"] >= as_of
    if late.any():
        warnings.append(f"{late.sum():,} sale(s) are on or after the stock date ({as_of:%d %b %Y}) "
                        "and are left out of demand.")
    st["as_of_date"] = as_of.date()

    # 5. Suppliers ------------------------------------------------------------
    if "suppliers" in t:
        sup = t["suppliers"]
        sup["supplier_id"] = _ids(sup["supplier_id"])
        sup = sup.dropna(subset=["supplier_id"]).drop_duplicates("supplier_id")
        sup["supplier_name"] = sup["supplier_name"].fillna(sup["supplier_id"]).astype(str)
    else:
        ids = [str(x) for x in p["supplier_id"].dropna().unique()]
        sup = pd.DataFrame({"supplier_id": ids, "supplier_name": [f"Supplier {x}" for x in ids]})
    extra = set(p["supplier_id"].dropna()) - set(sup["supplier_id"])
    if extra:
        sup = pd.concat([sup, pd.DataFrame({"supplier_id": sorted(extra),
                                            "supplier_name": ["Supplier " + x for x in sorted(extra)]})],
                        ignore_index=True)
    if "location" not in sup:
        sup["location"] = None

    # 6. Purchases (optional) -------------------------------------------------
    pu = pd.DataFrame(columns=["order_date", "receipt_date", "product_id", "supplier_id", "quantity", "unit_cost_inr"])
    if "purchases" in t:
        pu = t["purchases"]
        pu["product_id"] = _ids(pu["product_id"])
        pu["order_date"], pu["receipt_date"] = _date(pu["order_date"]), _date(pu["receipt_date"])
        pu["quantity"] = _num(pu["quantity"])
        bad = (pu["order_date"].isna() | pu["receipt_date"].isna() | (pu["receipt_date"] < pu["order_date"])
               | ~pu["product_id"].isin(known))
        if bad.any():
            warnings.append(f"{bad.sum():,} purchase row(s) have a missing date, arrive before they were "
                            "ordered, or are for unknown products; skipped.")
            pu = pu[~bad].copy()
        pmap = p.set_index("product_id")
        sid = _ids(pu["supplier_id"]) if "supplier_id" in pu else pd.Series(np.nan, index=pu.index)
        pu["supplier_id"] = sid.fillna(pu["product_id"].map(pmap["supplier_id"]))
        cost = _num(pu["unit_cost_inr"]) if "unit_cost_inr" in pu else pd.Series(np.nan, index=pu.index)
        pu["unit_cost_inr"] = cost.fillna(pu["product_id"].map(pmap["unit_cost_inr"]))
        pu["quantity"] = pu["quantity"].fillna(0)
    else:
        warnings.append("No purchases sheet, so the tool uses each product's promised lead time and "
                        "cannot score suppliers. Add purchase order and receipt dates to unlock that.")
    for c in ("order_date", "receipt_date"):
        pu[c] = pd.to_datetime(pu[c]).dt.date

    # 7. Customers (optional) -------------------------------------------------
    if "customers" in t:
        cu = t["customers"]
        cu["customer_id"] = _ids(cu["customer_id"])
        cu = cu.dropna(subset=["customer_id"]).drop_duplicates("customer_id")
    else:
        cu = pd.DataFrame({"customer_id": s["customer_id"].unique()})
        cu["customer_name"] = cu["customer_id"]
    if "segment" not in cu:
        cu["segment"] = None

    s["order_date"] = s["order_date"].dt.date
    below = (p["unit_price_inr"] < p["unit_cost_inr"]).sum()
    if below:
        warnings.append(f"{below} product(s) have a selling price below cost. Worth a look.")

    tables = {
        "products": p[["product_id", "product_name", "category", "unit_cost_inr", "unit_price_inr",
                       "supplier_id", "lead_time_days"]],
        "suppliers": sup[["supplier_id", "supplier_name", "location"]],
        "customers": cu[["customer_id", "customer_name", "segment"]],
        "sales": s[["invoice_no", "order_date", "customer_id", "product_id", "quantity", "unit_price_inr"]],
        "stock": st[["product_id", "on_hand_units", "as_of_date"]],
        "purchases": pu[["order_date", "receipt_date", "product_id", "supplier_id", "quantity", "unit_cost_inr"]],
    }
    summary = {"products": len(p), "sales_rows": len(s), "suppliers": len(sup), "purchases": len(pu),
               "first_sale": s["order_date"].min(), "last_sale": s["order_date"].max(), "as_of": as_of.date()}
    return tables, errors, warnings, summary


# ---------------------------------------------------------------------------
# Building the database
# ---------------------------------------------------------------------------
UPLOAD_DIR = Path(tempfile.gettempdir()) / "inventory-planner-uploads"


def build(tables: dict, fingerprint: bytes) -> Path:
    """One database per distinct upload, in the server's temp folder."""
    path = UPLOAD_DIR / f"{hashlib.sha1(fingerprint).hexdigest()[:16]}.db"
    if not path.exists():
        tmp = path.with_suffix(".building")
        tmp.unlink(missing_ok=True)
        build_db({k: tables[k] for k in TABLES}, tmp)
        tmp.replace(path)
    return path
