"""Checks that the maths does what the README says. Run:  python -m pytest -q"""
import io
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


# ---------------------------------------------------------------------------
# Currency: dollars by default, rupees on request
# ---------------------------------------------------------------------------
def test_money_formats_and_conversion():
    assert cfg.fmt(424613.4, "USD") == "$424,613"
    assert cfg.fmt(35242910, "INR") == "₹3,52,42,910"
    assert cfg.convert(8300, "INR", "USD") == pytest.approx(100)
    assert cfg.convert(100, "USD", "INR") == pytest.approx(8300)


def test_order_quantity_does_not_depend_on_currency():
    import analysis as an
    in_rupees = supply.eoq(1000, 1200)
    an.use_db(None, "USD")
    try:
        in_dollars = supply.eoq(1000, 1200 / cfg.INR_PER_USD)   # same item priced in dollars
    finally:
        an.use_db(None)
    assert in_dollars == pytest.approx(in_rupees)


# ---------------------------------------------------------------------------
# Messy exports: read anything, clean with a reason for every removed row
# ---------------------------------------------------------------------------
import raw_import as ri  # noqa: E402


def test_numbers_written_any_way():
    s = pd.Series(["₹ 1,25,000.00", "$1,234", "(45.00)", "120-", "12 pcs", "Rs.450", "", "abc"])
    got = ri.to_number(s).tolist()
    assert got[:6] == [125000.0, 1234.0, -45.0, -120.0, 12.0, 450.0]
    assert np.isnan(got[6]) and np.isnan(got[7])
    assert ri.to_number(pd.Series(["1.234,56", "2.000,00"])).tolist() == [1234.56, 2000.0]   # European style


def test_dates_written_any_way():
    d = ri.to_date(pd.Series(["25/04/2025", "03/04/2025", "45000", "2025-04-01", "13/45/2025", "hello"]))
    assert d[0] == pd.Timestamp("2025-04-25")          # day first, because 25 can't be a month
    assert d[1] == pd.Timestamp("2025-04-03")
    assert d[2] == pd.Timestamp("2023-03-15")          # an Excel serial number
    assert d[3] == pd.Timestamp("2025-04-01")
    assert pd.isna(d[4]) and pd.isna(d[5])


def _ugly_export() -> bytes:
    rows = [["Acme Traders Pvt Ltd", "", "", "", ""], ["Sales Register 2025", "", "", "", ""], ["", "", "", "", ""],
            ["Vch Date", "Particulars", "Item", "Qty", "Value"]]
    for i in range(120):
        day = f"{(i % 28) + 1:02d}-{(i // 28) % 12 + 1:02d}-2025"
        rows.append([day, "Shop A", " brake pad " if i % 5 == 0 else "Brake Pad", "4 Nos", "Rs. 1,000.00"])
    rows += [["01-02-2025", "Shop A", "Output CGST 9%", "", "Rs. 90.00"],
             ["02-02-2025", "Shop B", "Brake Pad", "-2", "(500.00)"],
             ["", "", "Grand Total", "", "Rs. 1,20,000.00"]]
    rows.append(rows[10])                                                  # a duplicate line
    buf = io.BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False)
    return buf.getvalue()


def test_ugly_export_is_read_cleaned_and_explained():
    table, _ = ri.read_any([("register.xlsx", _ugly_export())])
    assert list(table.columns) == ["Vch Date", "Particulars", "Item", "Qty", "Value"]   # header found under titles
    roles = ri.guess_roles(table)
    assert roles["date"] == "Vch Date" and roles["product_name"] == "Item" and roles["quantity"] == "Qty"
    sales, returns, log, fixes = ri.clean(table, roles)
    removed = dict(zip(log["Step"], log["Rows removed"]))
    assert removed["Exact duplicates"] == 1
    assert removed["Not products (tax, freight, fees, discounts)"] == 1
    assert removed["Returns and cancellations"] == 1
    assert sales["product"].nunique() == 1                 # ' brake pad ' and 'Brake Pad' are one product
    assert len(sales) == 120 and sales["price"].eq(250).all()   # price worked out as value / qty
    a = ri.analyse(sales, returns)
    assert a["products"] == 1


# ---------------------------------------------------------------------------
# Planner adjustments and supplier comparison
# ---------------------------------------------------------------------------
import adjust  # noqa: E402


def test_adjustments_apply_only_inside_their_window_and_multiply():
    adj = adjust.clean([
        {"product": "P09 · White Contrast Paint Aerosol", "change_pct": 30, "from_month": "Oct 2026", "to_month": None},
        {"product": adjust.ALL, "change_pct": -10, "from_month": "Oct 2026", "to_month": "Oct 2026"},
        {"product": "P01 · AC Yoke", "change_pct": None, "from_month": "Oct 2026"},          # incomplete: skipped
    ])
    assert len(adj) == 2 and adj[0][0] == "P09"
    assert adjust.multiplier(adj, "P09", "2026-10-05", "2026-10-20") == pytest.approx(1.3 * 0.9)
    assert adjust.multiplier(adj, "P05", "2026-10-05", "2026-10-20") == pytest.approx(0.9)
    assert adjust.multiplier(adj, "P09", "2026-12-01", "2026-12-31") == 1.0               # outside the window


def test_more_expected_demand_raises_the_reorder_point():
    base = supply.planning_table().set_index("product_id")
    adj = (("P09", 50.0, "2026-09", "2026-12", "trade show"),)
    up = supply.planning_table(adjustments=adj).set_index("product_id")
    assert up.loc["P09", "reorder_point"] > base.loc["P09", "reorder_point"]
    assert up.loc["P05", "reorder_point"] == base.loc["P05", "reorder_point"]             # others untouched


def test_supplier_comparison_counts_safety_stock():
    row = supply.planning_table().set_index("product_id").loc["P09"]   # a fast seller
    same_price = {"supplier_name": "X", "unit_cost": row.unit_cost_inr, "promised_lead_time_days": row.lead_time_days}
    t = supply.compare_suppliers("P09", [{**same_price, "reliability": "on_time"},
                                         {**same_price, "reliability": "S5"}])
    parts = t[["purchases", "ordering", "cycle_stock", "safety_stock_cost"]].sum(axis=1)
    assert np.allclose(parts, t["total"])
    # same price: the on-time supplier needs less safety stock than one as late as the importer
    assert t.loc[1, "safety_stock_units"] < t.loc[2, "safety_stock_units"]
    assert t.loc[1, "total"] < t.loc[2, "total"]
    sw = supply.supplier_switch_summary()
    assert set(sw["verdict"]) == {"Switch", "Stay"}                                        # the sample has both stories


def test_customer_view_is_consistent():
    import customers as cu
    s = cu.summary()
    assert len(s) == 40 and s["share"].sum() == pytest.approx(1.0)
    assert set(s["status"]) <= {"Active", "New", "Slowing", "Gone quiet"}
    conc = cu.concentration(s)
    assert 1 <= conc["n_for_80"] <= conc["customers"] and conc["top10_share"] >= conc["top1_share"]
    mix = cu.product_mix()
    # each product's demand is split across its customers: shares add up to 100%
    assert np.allclose(mix.groupby("product_id")["share_of_product"].sum(), 1.0)
