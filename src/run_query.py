"""
Run a SQL query and print the result as a table. For practising SQL.

  python src/run_query.py sql/practice/lesson1.sql       run a .sql file
  python src/run_query.py "SELECT * FROM products"       run a query typed inline
  python src/run_query.py --real sql/real_abc.sql        use the real-data database
  python src/run_query.py --tables                       list tables and their columns
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

import config as cfg

REAL_DB_PATH = cfg.ROOT / "data" / "real" / "retail.db"
MAX_ROWS = 50  # print at most this many rows so the screen doesn't flood


def show_tables(con: sqlite3.Connection) -> None:
    tables = con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (name,) in tables:
        rows = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({name})")]
        print(f"{name} ({rows} rows)\n    {', '.join(cols)}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a SQL query and print the result.")
    parser.add_argument("query", nargs="?", help="a .sql file path, or the SQL text itself")
    parser.add_argument("--real", action="store_true", help="use data/real/retail.db")
    parser.add_argument("--tables", action="store_true", help="list tables and columns")
    args = parser.parse_args()

    db = REAL_DB_PATH if args.real else cfg.DB_PATH
    if not db.exists():
        sys.exit(f"Database not found: {db}")

    with sqlite3.connect(db) as con:
        if args.tables:
            show_tables(con)
            return
        if not args.query:
            parser.error("give a .sql file or a query (or use --tables)")

        path = Path(args.query)
        sql = path.read_text(encoding="utf-8") if path.suffix == ".sql" else args.query

        try:
            df = pd.read_sql_query(sql, con)
        except Exception as e:  # show SQL mistakes plainly, without a long traceback
            sys.exit(f"SQL error: {e}")

    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(df.head(MAX_ROWS).to_string(index=False))
    print(f"\n{len(df)} rows" + (f" (showing first {MAX_ROWS})" if len(df) > MAX_ROWS else ""))


if __name__ == "__main__":
    main()
