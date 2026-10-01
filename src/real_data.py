"""
Real-data case study: UCI Online Retail II (a UK gift wholesaler, Dec 2009 - Dec 2011,
about 1 million invoice lines). Source: https://archive.ics.uci.edu/dataset/502/online+retail+ii
Chen, D. (2019). Online Retail II [Dataset]. UCI Machine Learning Repository.

Steps
  1. download()  get the file (about 45 MB) into data/real/
  2. clean()     documented cleaning steps; every step logs how many rows it removed
  3. load()      cleaned sales into SQLite (data/real/retail.db)
  4. analyses    ABC (SQL), ABC-XYZ, monthly trend, returns, forecast backtest

Run:  python src/real_data.py
"""
import sqlite3
import urllib.request
import zipfile

import numpy as np
import pandas as pd

import analysis as an
import config as cfg

REAL_DIR = cfg.ROOT / "data" / "real"
CSV = REAL_DIR / "online_retail_ii.csv.gz"
DB = REAL_DIR / "retail.db"
URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
PRODUCT_CODE = r"^\d{5}[A-Za-z]*$"   # real products: 5 digits plus an optional letter (colour/size)


# ---------------------------------------------------------------------------
# 1. Get the data
# ---------------------------------------------------------------------------
def download():
    """Download and convert once. The Excel file has two sheets (one per year)."""
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    if CSV.exists():
        return
    z = REAL_DIR / "online_retail_ii.zip"
    if not z.exists():
        print("Downloading about 45 MB from UCI...")
        urllib.request.urlretrieve(URL, z)
    zipfile.ZipFile(z).extractall(REAL_DIR)
    print("Reading Excel (takes 1-2 minutes)...")
    sheets = pd.read_excel(REAL_DIR / "online_retail_II.xlsx", sheet_name=None,
                           dtype={"Invoice": str, "StockCode": str})
    pd.concat(sheets.values(), ignore_index=True).to_csv(CSV, index=False, compression="gzip")


def read_raw() -> pd.DataFrame:
    df = pd.read_csv(CSV, dtype={"Invoice": str, "StockCode": str}, parse_dates=["InvoiceDate"])
    return df.rename(columns={"Invoice": "invoice", "StockCode": "stock_code", "Description": "description",
                              "Quantity": "quantity", "InvoiceDate": "invoice_date", "Price": "price",
                              "Customer ID": "customer_id", "Country": "country"})


# ---------------------------------------------------------------------------
# 2. Clean
# ---------------------------------------------------------------------------
def clean(df: pd.DataFrame):
    """
    Returns (sales, returns, log). Each step removes rows for a stated reason, and the
    log records how many rows and how much revenue each step took out.
    """
    log = []

    def step(name, keep, why):
        nonlocal df
        removed = df[~keep]
        log.append({"step": name, "rows_removed": len(removed),
                    "value_removed_gbp": float((removed.quantity * removed.price).abs().sum()), "why": why})
        df = df[keep]

    df["stock_code"] = df["stock_code"].str.upper().str.strip()
    log.append({"step": "Raw file", "rows_removed": 0, "value_removed_gbp": 0.0,
                "why": f"{len(df):,} invoice lines to start with"})

    step("Exact duplicates", ~df.duplicated(), "Same invoice line entered twice")

    cancelled = df["invoice"].str.startswith("C")
    returns = df[cancelled].copy()
    step("Cancellations", ~cancelled, "Invoices starting with C are returns/cancellations; kept aside for the returns analysis")

    step("Accounting adjustments", df["invoice"].str.match(r"^\d+$"), "Invoices starting with A are bad-debt adjustments, not sales")
    step("Non-product codes", df["stock_code"].str.match(PRODUCT_CODE),
         "Postage, carriage, manual entries, bank charges, Amazon fees, gift vouchers, test codes")
    step("Zero or negative quantity", df["quantity"] > 0, "Stock write-offs and damage notes (e.g. 'thrown away'), not customer demand")
    step("Zero or negative price", df["price"] > 0, "Free samples and data errors")

    # Orders that were keyed in and then fully cancelled were never real demand.
    r = returns.assign(quantity=-returns["quantity"])
    key = ["customer_id", "stock_code", "quantity"]
    matched = df.merge(r[key].drop_duplicates(), on=key, how="left", indicator=True)["_merge"].eq("both").to_numpy()
    big = (df["quantity"] >= 1000).to_numpy()
    step("Large orders later cancelled", ~(matched & big),
         "Orders of 1,000+ units with an identical cancellation (e.g. 80,995 paper crafts), keyed in by mistake")

    df = df.copy()
    df["revenue_gbp"] = df["quantity"] * df["price"]
    # One description per product: the most common one (some lines have typos or blanks)
    names = (df.dropna(subset=["description"]).groupby("stock_code")["description"]
               .agg(lambda s: s.value_counts().index[0]).str.strip().str.title())
    df["description"] = df["stock_code"].map(names).fillna("(no description)")
    log.append({"step": "Clean sales", "rows_removed": 0, "value_removed_gbp": 0.0,
                "why": f"{len(df):,} lines kept. Missing customer IDs ({df.customer_id.isna().sum():,} lines) "
                       "are kept for demand but left out of customer analysis"})
    return df, returns, pd.DataFrame(log)


# ---------------------------------------------------------------------------
# 3. Load into SQLite
# ---------------------------------------------------------------------------
def load(sales: pd.DataFrame, returns: pd.DataFrame):
    with sqlite3.connect(DB) as con:
        s = sales.assign(invoice_date=sales.invoice_date.dt.strftime("%Y-%m-%d"))
        s[["invoice", "invoice_date", "stock_code", "description", "quantity", "price",
           "revenue_gbp", "customer_id", "country"]].to_sql("sales", con, if_exists="replace", index=False)
        rt = returns.assign(invoice_date=returns.invoice_date.dt.strftime("%Y-%m-%d"),
                            value_gbp=(-returns.quantity * returns.price))
        rt[["invoice", "invoice_date", "stock_code", "quantity", "price", "value_gbp", "customer_id",
            "country"]].to_sql("returns", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX IF NOT EXISTS ix_s_code ON sales(stock_code)")
        con.execute("CREATE INDEX IF NOT EXISTS ix_s_date ON sales(invoice_date)")


def sql(name: str, params=None) -> pd.DataFrame:
    with sqlite3.connect(DB) as con:
        return pd.read_sql_query((cfg.SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"), con,
                                 params=params or {})


# ---------------------------------------------------------------------------
# 4. Analyses
# ---------------------------------------------------------------------------
def abc() -> pd.DataFrame:
    return sql("real_abc")


def abc_xyz() -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    XYZ = how predictable each product's demand is, using the coefficient of
    variation (CV = std / mean) of monthly units over the last 12 months:
      X  CV <= 0.5   steady    -> forecast and stock confidently
      Y  0.5 - 1.0   variable  -> more safety stock
      Z  CV > 1.0    erratic   -> make/buy to order or keep a small buffer
    Combined with ABC it tells you where planning effort pays off.
    """
    a = abc()
    w = sql("real_monthly_units")
    w = w[(w.month >= "2010-12-01") & (w.month < "2011-12-01")]
    grid = w.pivot_table(index="month", columns="stock_code", values="units", aggfunc="sum").fillna(0)
    cv = (grid.std(ddof=1) / grid.mean()).rename("cv")
    t = a.merge(cv, left_on="stock_code", right_index=True, how="left")
    t["xyz_class"] = pd.cut(t["cv"], [-np.inf, 0.5, 1.0, np.inf], labels=["X", "Y", "Z"]).astype(str)
    matrix = (t.groupby(["abc_class", "xyz_class"])
               .agg(products=("stock_code", "count"), revenue_gbp=("revenue_gbp", "sum"))
               .reset_index())
    matrix["revenue_share"] = matrix["revenue_gbp"] / matrix["revenue_gbp"].sum()
    return t, matrix


def monthly() -> pd.DataFrame:
    m = sql("real_monthly")
    m["month"] = pd.to_datetime(m["month"])
    return m


def returns_summary() -> pd.DataFrame:
    return sql("real_returns_by_country")


def _ses(hist: pd.DataFrame, alpha: float = 0.3) -> pd.Series:
    """Simple exponential smoothing: recent months count more, older months fade out."""
    level = hist.iloc[0].copy()
    for _, row in hist.iloc[1:].iterrows():
        level = alpha * row + (1 - alpha) * level
    return level


def forecast_backtest(top_n: int = 200, test_months: int = 6) -> dict:
    """
    Compare forecasting methods on the top products by revenue. Each test month is
    forecast using only the months before it (no peeking). Two scores per method:
      product error  = WAPE across products (what you'd stock each item by)
      total error    = error on the sum of all products (what you'd plan cash/capacity by)
    Seasonality is learned from the first 12 months only.
    """
    s = sql("real_monthly_units")
    top = abc().head(top_n)["stock_code"]
    m = (s[s.stock_code.isin(top)]
           .pivot_table(index="month", columns="stock_code", values="units", aggfunc="sum").fillna(0))
    m.index = pd.to_datetime(m.index)
    m = m[m.index < pd.Timestamp("2011-12-01")]       # December 2011 is only 9 days
    s_idx = an.seasonal_index(m.iloc[:12])

    def deseason(h):
        return h.div(s_idx.loc[h.index.month].values, axis=0)

    methods = {
        "Same as last month": lambda h, t: h.iloc[-1],
        "Same month last year": lambda h, t: h.iloc[-12],
        "3-month average": lambda h, t: h.tail(3).mean(),
        "Exponential smoothing": lambda h, t: _ses(h),
        "3-month average x seasonality": lambda h, t: an._forecast_from(h, t, s_idx),
        "Exp. smoothing x seasonality": lambda h, t: _ses(deseason(h)) * s_idx.loc[t.month],
    }
    rows, monthly_rows = [], []
    for name, f in methods.items():
        err = total_err = actual_total = 0.0
        for i in range(len(m) - test_months, len(m)):
            hist, actual = m.iloc[:i], m.iloc[i]
            pred = f(hist, m.index[i]).clip(lower=0)
            err += (pred - actual).abs().sum()
            total_err += abs(pred.sum() - actual.sum())
            actual_total += actual.sum()
            monthly_rows.append({"method": name, "month": m.index[i], "actual": actual.sum(), "forecast": pred.sum()})
        rows.append({"method": name, "product_wape": err / actual_total, "total_error": total_err / actual_total})
    table = pd.DataFrame(rows)
    return {"table": table, "monthly": pd.DataFrame(monthly_rows), "products": top_n,
            "months": f"{m.index[-test_months]:%b %Y} to {m.index[-1]:%b %Y}",
            "best_product": table.loc[table.product_wape.idxmin()],
            "best_total": table.loc[table.total_error.idxmin()]}


def build():
    download()
    sales, returns, log = clean(read_raw())
    load(sales, returns)
    log.to_csv(REAL_DIR / "cleaning_log.csv", index=False)
    return log


def cleaning_log() -> pd.DataFrame:
    return pd.read_csv(REAL_DIR / "cleaning_log.csv")


def available() -> bool:
    return DB.exists() and (REAL_DIR / "cleaning_log.csv").exists()


if __name__ == "__main__":
    pd.set_option("display.width", 180)
    log = build()
    print(log.to_string(index=False))
    a = abc()
    share = a.loc[a.abc_class == "A"]
    print(f"\nABC: {len(share)} of {len(a)} products ({len(share) / len(a):.0%}) make 80% of revenue")
    t, mx = abc_xyz()
    print(mx.to_string(index=False))
    print(returns_summary().head(8).to_string(index=False))
    b = forecast_backtest()
    print(f"\nForecast backtest, top {b['products']} products, {b['months']}")
    print(b["table"].round(3).to_string(index=False))
