"""Project settings, all in one place."""
from pathlib import Path

# Folders
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"          # real company data, if any (never committed)
SAMPLE_DIR = ROOT / "sample_data"        # made-up data for the public repo
DB_PATH = ROOT / "data" / "inventory.db"  # SQLite database
SQL_DIR = ROOT / "sql"

# Money: every amount is stored in rupees. USD is shown next to it.
INR_PER_USD = 83.0  # update this one number when the rate changes

# Inventory rules
DEMAND_WINDOW_DAYS = 90   # average demand is measured over the last 90 days
SERVICE_LEVEL_Z = 1.65    # safety stock covers ~95% of demand swings
IDLE_DAYS = 180           # no sale for 6 months = idle stock
OVERSTOCK_MONTHS = 6      # more than 6 months of stock on hand = overstock
MAKE_TO_ORDER_BELOW = 1   # sells under 1 unit/month = build to order, don't stock

# Costs used for order quantity and the service-level simulation
ORDER_COST_INR = 1500     # cost of placing one purchase order (admin, freight, receiving)
HOLDING_RATE = 0.25       # yearly cost of holding stock, as a share of its value
ON_TIME_GRACE_DAYS = 2    # a delivery up to 2 days late still counts as on time


def money(inr: float) -> str:
    """Format a rupee amount as '₹1,23,456 (≈$1,487)'."""
    return f"{inr_fmt(inr)} (≈${inr / INR_PER_USD:,.0f})"


def inr_fmt(inr: float) -> str:
    """Indian digit grouping: 1,23,45,678."""
    n = int(round(inr))
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return f"{sign}₹{s}"
    last3, rest = s[-3:], s[:-3]
    groups = []
    while len(rest) > 2:
        groups.insert(0, rest[-2:])
        rest = rest[:-2]
    if rest:
        groups.insert(0, rest)
    return f"{sign}₹{','.join(groups)},{last3}"
