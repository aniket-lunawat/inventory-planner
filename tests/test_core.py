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


# ---------------------------------------------------------------------------
# Upload: anyone's data, checked before it is used
# ---------------------------------------------------------------------------
import upload  # noqa: E402


def _tiny_company(days=200):
    dates = pd.date_range("2026-01-01", periods=days, freq="D")
    return {
        "products": pd.DataFrame({"SKU": [1001.0, 1002.0], "Product Name": ["Bolt", "Nut"],
                                  "Unit Cost": ["₹1,200", 50], "Lead Time": [10, None]}),
        "sales": pd.DataFrame({"Date": list(dates) * 2, "SKU": [1001] * days + ["1002"] * days,
                               "Qty": [2] * days + [5] * days, "Price": [1500] * days + [80] * days}),
        "stock": pd.DataFrame({"product_id": ["1001"], "on_hand_units": [30]}),
    }


def test_upload_accepts_messy_but_usable_data():
    tables, errors, warnings, summary = upload.validate(_tiny_company())
    assert errors == []
    p = tables["products"].set_index("product_id")
    assert list(p.index) == ["1001", "1002"]                 # 1001.0 from Excel matches "1001" in sales
    assert p.loc["1001", "unit_cost_inr"] == 1200            # "₹1,200" read as a number
    assert p.loc["1002", "lead_time_days"] == upload.DEFAULT_LEAD_TIME
    assert len(tables["sales"]) == 400
    stock = tables["stock"].set_index("product_id")["on_hand_units"]
    assert stock["1002"] == 0                                 # missing stock row counted as 0
    assert any("no stock row" in w for w in warnings)
    assert any("No purchases sheet" in w for w in warnings)


def test_upload_rejects_missing_sheet_and_short_history():
    raw = _tiny_company()
    del raw["stock"]
    assert any("stock" in e for e in upload.validate(raw)[1])
    assert any("at least 3 months" in e for e in upload.validate(_tiny_company(days=40))[1])


def test_uploaded_data_is_analysed_without_touching_the_sample(tmp_path, monkeypatch):
    import analysis as an
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    tables, errors, _, _ = upload.validate(_tiny_company())
    path = upload.build(tables, b"tiny")
    an.use_db(path)
    try:
        plan = supply.planning_table()
        assert set(plan.product_id) == {"1001", "1002"}
        assert supply.supplier_scorecard().empty               # no purchases, no scorecard
    finally:
        an.use_db(None)
    assert len(supply.planning_table()) == 24                  # sample company unchanged
