"""
Load the CSV files into the SQLite database, after basic cleaning and checks.

Uses the real files in data/raw/ if they exist, otherwise the sample data.
Run:  python src/load_db.py
"""
import sqlite3

import pandas as pd

from config import DB_PATH, RAW_DIR, SAMPLE_DIR, SQL_DIR

TABLES = ["suppliers", "products", "customers", "sales", "stock", "purchases"]


def pick_source():
    if all((RAW_DIR / f"{t}.csv").exists() for t in TABLES):
        return RAW_DIR, "REAL data from data/raw"
    return SAMPLE_DIR, "sample data"


def clean(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Small, explainable cleaning steps. Each one prints what it changed."""
    before = len(df)
    df.columns = [c.strip().lower() for c in df.columns]
    for c in df.select_dtypes(include=["object", "string"]).columns:
        df[c] = df[c].str.strip()
    df = df.drop_duplicates()
    if name == "sales":
        df["order_date"] = pd.to_datetime(df["order_date"], dayfirst=False).dt.date
        df = df[df["quantity"] > 0]            # returns handled separately later
        df = df.dropna(subset=["product_id", "quantity", "unit_price_inr"])
    if name == "purchases":
        df["order_date"] = pd.to_datetime(df["order_date"]).dt.date
        df["receipt_date"] = pd.to_datetime(df["receipt_date"]).dt.date
        df = df[df["receipt_date"] >= df["order_date"]]   # can't arrive before it's ordered
    if name == "stock":
        df["on_hand_units"] = df["on_hand_units"].clip(lower=0)
    dropped = before - len(df)
    if dropped:
        print(f"  {name}: removed {dropped} bad/duplicate rows")
    return df


def check(con):
    """Catch data problems early instead of in the dashboard."""
    q = {
        "sales with unknown product": "SELECT COUNT(*) FROM sales WHERE product_id NOT IN (SELECT product_id FROM products)",
        "products with no stock row": "SELECT COUNT(*) FROM products WHERE product_id NOT IN (SELECT product_id FROM stock)",
        "products priced below cost": "SELECT COUNT(*) FROM products WHERE unit_price_inr < unit_cost_inr",
    }
    for label, sql in q.items():
        n = con.execute(sql).fetchone()[0]
        print(f"  check - {label}: {n}" + ("  <-- look at this" if n else ""))


def build_db(frames: dict, path) -> None:
    """Create a fresh database at `path` from one DataFrame per table (already cleaned)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    for t in TABLES:
        frames[t].to_sql(t, con, if_exists="append", index=False)
    con.commit()
    con.close()


def main():
    src, label = pick_source()
    print(f"Loading {label} from {src}")
    frames = {}
    for t in TABLES:
        frames[t] = clean(t, pd.read_csv(src / f"{t}.csv"))
        print(f"  {t}: {len(frames[t])} rows")
    build_db(frames, DB_PATH)
    with sqlite3.connect(DB_PATH) as con:
        check(con)
    print(f"Database ready: {DB_PATH}")


if __name__ == "__main__":
    main()
