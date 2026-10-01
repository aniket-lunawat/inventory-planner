"""Checks that the maths does what the README says. Run:  python -m pytest -q"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import config as cfg  # noqa: E402
import supply  # noqa: E402
import real_data as rd  # noqa: E402


def test_indian_number_format():
    assert cfg.inr_fmt(35242910) == "₹3,52,42,910"
    assert cfg.inr_fmt(999) == "₹999"
    assert cfg.inr_fmt(100000) == "₹1,00,000"


def test_eoq_textbook_example():
    # D = 1,000 units/yr, S = ₹1,500/order, H = 25% of ₹1,200 = ₹300/unit/yr
    # EOQ = sqrt(2 * 1000 * 1500 / 300) = 100
    assert supply.eoq(1000, 1200) == pytest.approx(100)


def test_safety_stock_reduces_to_simple_formula_when_supplier_is_reliable():
    # No lead-time variation: SS = z * sd * sqrt(LT)
    assert supply.safety_stock(1.65, 10, 4, 9, 0) == pytest.approx(1.65 * 4 * 3)


def test_late_suppliers_need_more_safety_stock():
    reliable = supply.safety_stock(1.65, 10, 4, 9, 0)
    unreliable = supply.safety_stock(1.65, 10, 4, 9, 3)
    assert unreliable > reliable


def test_higher_service_level_means_more_stock_and_fewer_stockouts():
    rng = np.random.default_rng(0)
    hist = rng.poisson(5, 200).astype(float)
    lts = np.array([10, 12, 15, 9, 11], dtype=float)
    low = supply._simulate_product(hist, lts, s=50, q=40, start_stock=90, days=365, runs=200, rng=rng)
    high = supply._simulate_product(hist, lts, s=80, q=40, start_stock=120, days=365, runs=200, rng=rng)
    assert high[0] > low[0]      # fill rate up
    assert high[1] < low[1]      # stockout days down
    assert high[2] > low[2]      # average stock up


def test_planning_table_is_consistent():
    t = supply.planning_table()
    stocked = t[~t.status.isin(["Not selling", "Make to order"])]
    assert (stocked.reorder_point >= stocked.safety_stock).all()
    assert (t.loc[t.status == "Order now", "on_hand_units"]
            <= t.loc[t.status == "Order now", "reorder_point"]).all()
    assert (t.loc[t.status != "Order now", "suggested_order_units"] == 0).all()


def _toy_raw():
    rows = [
        # invoice, code, desc, qty, price, customer
        ("1001", "85048", "Mug", 6, 2.5, 1.0),
        ("1001", "85048", "Mug", 6, 2.5, 1.0),        # exact duplicate
        ("C1002", "85048", "Mug", -2, 2.5, 1.0),      # cancellation
        ("A1003", "B", "Adjust bad debt", 1, -500, None),
        ("1004", "POST", "Postage", 1, 18.0, 2.0),    # not a product
        ("1005", "22041", "Card", -5, 0.0, None),     # write-off
        ("1006", "22041", "Card", 3, 0.0, 3.0),       # zero price
        ("1007", "23843", "Paper craft", 80995, 2.08, 16446.0),
        ("C1008", "23843", "Paper craft", -80995, 2.08, 16446.0),
        ("1009", "22041", "Card", 12, 0.42, None),    # missing customer: kept
    ]
    df = pd.DataFrame(rows, columns=["invoice", "stock_code", "description", "quantity", "price", "customer_id"])
    df["invoice_date"] = pd.Timestamp("2011-01-05")
    df["country"] = "United Kingdom"
    return df


def test_cleaning_steps():
    sales, returns, log = rd.clean(_toy_raw())
    assert sorted(sales.invoice) == ["1001", "1009"]
    assert set(returns.invoice) == {"C1002", "C1008"}
    removed = dict(zip(log.step, log.rows_removed))
    assert removed["Exact duplicates"] == 1
    assert removed["Large orders later cancelled"] == 1
    assert sales.revenue_gbp.sum() == pytest.approx(6 * 2.5 + 12 * 0.42)
