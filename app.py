"""
Inventory planning dashboard for a small manufacturer (simulated data).
Run:  streamlit run app.py
"""
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))
import analysis as an  # noqa: E402
import config as cfg  # noqa: E402
import supply  # noqa: E402
import real_data as rd  # noqa: E402
import upload  # noqa: E402
import raw_view  # noqa: E402
import adjust  # noqa: E402
import customers as cust  # noqa: E402

# After a git push the host re-runs app.py but can keep OLD copies of the files in src/ in memory,
# so new app code would call old functions and crash. Reload them (in dependency order) on each run.
import importlib  # noqa: E402
import load_db  # noqa: E402
import raw_import  # noqa: E402
for _mod in (cfg, an, adjust, supply, cust, load_db, upload, rd, raw_import, raw_view):
    importlib.reload(_mod)

st.set_page_config(page_title="Inventory Planner", page_icon="📦", layout="wide")

# Colours (validated chart palette + status colours)
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRID, INK2 = "#e7e6e2", "#52514e"
STATUS_ICON = {"Order now": "🔴 Order now", "Overstock": "🟠 Overstock",
               "Not selling": "⚫ Not selling", "Make to order": "🔵 Make to order", "OK": "🟢 OK"}


# ---------------------------------------------------------------------------
# Data (cached so the page is fast)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Sidebar: which data to show (the simulated company, or the visitor's own)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def template_bytes():
    return upload.template_xlsx()


@st.cache_data(show_spinner="Checking your data...")
def check_upload(files):
    raw, blob = upload.read_upload(files)
    tables, errors, warnings, summary = upload.validate(raw)
    path = str(upload.build(tables, blob)) if tables else None
    return path, errors, warnings, summary


EXAMPLE = cfg.SAMPLE_DIR / "example_upload.xlsx"
MESSY_EXAMPLE = cfg.SAMPLE_DIR / "example_messy_export.xlsx"
SAMPLE, TIDY, MESSY = "Sample company (simulated)", "My data: tidy template", "My data: messy export (any format)"
with st.sidebar:
    show_label = st.radio("Show money in", ["US dollars ($)", "Indian rupees (₹)"], horizontal=True)
    SHOW = "USD" if show_label.startswith("US") else "INR"
    st.header("Data")
    mode = st.radio("Show results for", [SAMPLE, MESSY, TIDY], label_visibility="collapsed",
                    captions=["", "Any sales export, as messy as it is", "Our Excel template, filled in"])
    files, stock_files = [], []
    if mode == MESSY:
        st.markdown("**1. Upload your sales export.** Excel, CSV, or zipped. Title rows, totals, odd dates, "
                    "returns, tax lines and typos are fine.")
        up = st.file_uploader("Sales export", type=["xlsx", "xls", "xlsm", "csv", "txt", "tsv", "gz", "zip"],
                              accept_multiple_files=True, label_visibility="collapsed", key="messy_up")
        files = [(f.name, f.getvalue()) for f in up or []]
        file_ccy = st.selectbox("Currency of your file", ["USD", "INR", "GBP", "EUR"],
                                format_func=lambda c: {"USD": "US dollar ($)", "INR": "Indian rupee (₹)",
                                                       "GBP": "British pound (£)", "EUR": "Euro (€)"}[c])
        st.download_button("No file handy? Download a messy example", MESSY_EXAMPLE.read_bytes(),
                           "example_messy_sales_export.xlsx", width="stretch",
                           help="A made-up bike parts seller's accounting report, with every common mess in it.")
        st.markdown("**2. Add stock (optional).** Unlocks reorder alerts. Get the pre-filled sheet from the "
                    "*Unlock reorder alerts* tab.")
        st_up = st.file_uploader("Stock sheet", type=["xlsx", "csv"], accept_multiple_files=False,
                                 label_visibility="collapsed", key="stock_up")
        stock_files = [(st_up.name, st_up.getvalue())] if st_up else []
        st.caption("Your files are only used to draw this page. They are not kept, and other visitors can't see them.")
    if mode == TIDY:
        st.markdown("**1. Get the template.** Fill in 3 sheets: products, sales, stock. "
                    "Purchases and suppliers are optional.")
        st.download_button("Download blank template (.xlsx)", template_bytes(),
                           "inventory_planner_template.xlsx", width="stretch")
        st.download_button("Or download an example to try", EXAMPLE.read_bytes(),
                           "example_bike_parts_distributor.xlsx", width="stretch",
                           help="A made-up bicycle parts distributor, already in the right format.")
        st.markdown("**2. Upload it.** One Excel file, or CSVs named products.csv, sales.csv and so on.")
        file_ccy_label = st.radio("Amounts in my file are in", ["US dollars", "Indian rupees"], horizontal=True)
        up = st.file_uploader("Upload", type=["xlsx", "csv"], accept_multiple_files=True,
                              label_visibility="collapsed")
        files = [(f.name, f.getvalue()) for f in up or []]
        st.caption("Your file is only used to draw this page. It is not kept, and other visitors can't see it.")

db_path, upload_warnings, upload_summary = None, [], {}
data_ccy = "INR"   # the simulated company's amounts are stored in rupees
if mode == MESSY:
    if not files:
        st.title("Inventory Planner")
        st.info("**Upload any sales export in the sidebar**: from Tally, QuickBooks, SAP, Shopify, a till, or a "
                "spreadsheet someone keeps by hand. One row per sale or invoice line is all it needs.")
        st.markdown("""
**What it copes with**
- Title lines above the header, page breaks with repeated headers, subtotal and grand total rows
- Any column names (Qty, Units, Rate, MRP, Particulars, StockCode ...): it works out which column is which, and you can correct it
- Dates in any format, including Excel numbers; amounts like ₹1,25,000.00, $1,234, (45.00) or 12 pcs
- The same product spelt differently, duplicates, returns and credit notes, tax/freight/discount lines,
  free samples, and huge orders keyed in by mistake and cancelled

**What you get straight away:** a cleaning report, the cleaned file to download, sales trend, ABC-XYZ, a tested
forecast, products slowing down or stopped, returns and customer concentration. Add your stock levels and you get
reorder alerts too.

No file handy? Download the messy example in the sidebar and upload it.""")
        st.stop()
    if not stock_files:
        raw_view.render(files, file_ccy)
        st.stop()
    tables, notes = raw_view.unlocked_tables(files, stock_files)
    if tables is None:
        st.title("Inventory Planner")
        st.error(notes[0])
        st.stop()
    t_, errors, upload_warnings, upload_summary = upload.validate(tables)
    if errors:
        st.title("Inventory Planner")
        st.error("**The stock sheet couldn't be used yet:**\n\n" + "\n".join(f"- {e}" for e in errors))
        st.stop()
    upload_warnings = notes + upload_warnings
    db_path = str(upload.build(t_, b"".join(n.encode() + d for n, d in files + stock_files)))
    data_ccy = file_ccy
    if data_ccy in ("GBP", "EUR"):
        SHOW = data_ccy          # only dollars and rupees are converted; other currencies are shown as they are
elif mode == TIDY:
    if not files:
        st.title("Inventory Planner")
        st.info("**Use the sidebar to upload your data.** Download the template, fill in your products, "
                "sales and stock, and upload it. Every tab will then show results for your business. "
                "No data yet? Download the example file and upload that.")
        st.stop()
    db_path, errors, upload_warnings, upload_summary = check_upload(files)
    if errors:
        st.title("Inventory Planner")
        st.error("**Your file couldn't be used yet.** Fix these and upload it again:\n\n" +
                 "\n".join(f"- {e}" for e in errors))
        for w in upload_warnings:
            st.warning(w)
        st.stop()
    data_ccy = "USD" if file_ccy_label == "US dollars" else "INR"
an.use_db(db_path, data_ccy)   # None = the simulated company
adjustments = adjust.clean(st.session_state.get("adj_rows", []))   # planner's demand adjustments


@st.cache_data(show_spinner="Crunching the numbers...")
def load(db, ccy, adj=()):
    an.use_db(db, ccy)
    if db is None and not cfg.DB_PATH.exists():
        import load_db
        load_db.main()
    fc, acc = an.forecast(adj)
    return (an.abc(), an.monthly_sales(), an.idle_stock(), an.reorder_table(adj), fc, acc, an.today(),
            supply.supplier_scorecard(), supply.turnover())


@st.cache_data(show_spinner="Looking at your customers...")
def customer_data(db, ccy):
    an.use_db(db, ccy)
    s_ = cust.summary()
    mix_ = cust.product_mix()
    return s_, cust.concentration(s_), mix_, cust.at_risk(s_, mix_)


@st.cache_data(show_spinner="Comparing suppliers on total yearly cost...")
def switch_summary(db, ccy, adj=()):
    an.use_db(db, ccy)
    return supply.supplier_switch_summary(adj)


@st.cache_data(show_spinner="Simulating a year of orders, 300 times per service level...")
def what_if(db, ccy):
    an.use_db(db, ccy)
    return supply.simulate_service_levels()


abc, monthly, idle, reorder, fc, acc, today, suppliers, turns = load(db_path, data_ccy, adjustments)
using_real = all((cfg.RAW_DIR / f"{t}.csv").exists() for t in ["sales", "products", "stock"])
uploaded = db_path is not None


def pct(v):
    return "n/a" if v is None else f"{v:.0%}"


def money(v):
    """An amount from the data, converted to the chosen display currency and formatted."""
    return cfg.fmt(cfg.convert(v, data_ccy, SHOW), SHOW)


def price(v):
    """A unit price: keeps cents (or paise) for small amounts, unlike money()."""
    x = cfg.convert(v, data_ccy, SHOW)
    return f"{cfg.fmt(0, SHOW)[0]}{x:,.2f}" if abs(x) < 100 else money(v)


def m(v):
    """Money for text shown as Markdown: '$' is escaped, or two of them would render as maths."""
    return money(v).replace("$", "\\$")


# charts: dollars in thousands, rupees in lakh (1 lakh = 100,000)
CCY_NAME = {"USD": "US dollars", "INR": "rupees", "GBP": "pounds", "EUR": "euros"}
SCALE, SCALE_LABEL = (1e5, "₹ lakh") if SHOW == "INR" else (1e3, f"{cfg.fmt(0, SHOW)[0]} thousand")


def scaled(v):
    return cfg.convert(v, data_ccy, SHOW) / SCALE


def style_fig(fig, height=340):
    fig.update_layout(
        height=height, margin=dict(l=8, r=8, t=8, b=8), plot_bgcolor="white",
        paper_bgcolor="white", font=dict(color=INK2, size=13), showlegend=False,
        hoverlabel=dict(bgcolor="white", font_size=13), bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


# ---------------------------------------------------------------------------
# Header + headline numbers
# ---------------------------------------------------------------------------
st.title("Inventory Planner")
if SHOW == data_ccy:
    st.caption(f"Stock as of {today:%d %b %Y}. Money in {CCY_NAME[SHOW]}.")
else:
    st.caption(f"Stock as of {today:%d %b %Y}. Money in {CCY_NAME[SHOW]}, "
               f"converted at ₹{cfg.INR_PER_USD:.0f} = \\$1.")
if uploaded:
    u = upload_summary
    st.success(f"**Your data:** {u['products']} products, {u['sales_rows']:,} sales lines from "
               f"{u['first_sale']:%d %b %Y} to {u['last_sale']:%d %b %Y}, {u['purchases']:,} deliveries. "
               f"Amounts read as {CCY_NAME[data_ccy]} (change this in the sidebar).")
    if upload_warnings:
        with st.expander(f"{len(upload_warnings)} thing(s) to check in your data"):
            for w in upload_warnings:
                st.markdown(f"- {w}")
elif not using_real:
    st.info("**Simulated data.** A made-up company modeled on a small Indian manufacturer of magnetic "
            "inspection tools: 24 products, 40 customers, 2 years of orders. No real company figures are shown.")

if adjustments:
    st.warning(f"**{len(adjustments)} demand adjustment(s) active.** They change next month's forecast and the "
               "reorder points of the products they cover. See the ✏️ Adjust demand tab.")

order_now = reorder[reorder.status == "Order now"]
stuck = idle.stock_value_inr.sum() + reorder.loc[reorder.status == "Overstock", "stock_value_inr"].sum()
rev12 = abc.revenue_inr.sum()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric("Sales, last 12 months", money(rev12))
with k2:
    st.metric("Products to reorder now", f"{len(order_now)}")
    st.caption(f"Order worth {m(order_now.suggested_order_value_inr.sum())}")
with k3:
    st.metric("Money stuck in slow stock", money(stuck))
    st.caption(f"Idle, or more than {cfg.OVERSTOCK_MONTHS} months of stock")
with k4:
    if acc["wape"] is None:
        st.metric("Forecast accuracy", "n/a")
        st.caption("Needs 4 or more full months of sales to test")
    else:
        st.metric("Forecast accuracy", f"{1 - acc['wape']:.0%}")
        st.caption(f"Tested on the last {acc['months_tested']} months (simple guess: {1 - acc['naive_wape']:.0%})")

tab1, tab2, tab3, tab_adj, tab_cust, tab4, tab5, tab6, tab7 = st.tabs([
    "🔴 Reorder now", "📈 What sells", "🔮 Next month", "✏️ Adjust demand", "👥 Customers", "📦 Stuck stock",
    "🚚 Suppliers",
    "⚖️ Service level what-if", "🇬🇧 Real data: UK wholesaler"])

# ---------------------------------------------------------------------------
# 1. Reorder
# ---------------------------------------------------------------------------
with tab1:
    st.subheader(f"{len(order_now)} products are at or below their reorder point")
    st.write("Stock left will run out before a new batch can arrive. Order these now. "
             "Suggested quantity is the economic order quantity, or more if needed to get back above the reorder point.")
    show = reorder.copy()
    show["Status"] = show.status.map(STATUS_ICON)
    show["Days of stock left"] = show.days_of_cover.apply(lambda d: "—" if d == float("inf") else f"{d:.0f}")
    show["Order value"] = show.suggested_order_value_inr.apply(lambda v: money(v) if v else "")
    show["suggested_order_units"] = show.suggested_order_units.apply(lambda q: str(q) if q else "")
    show["Actual lead time (days)"] = show.lt_mean.round(0).astype(int)
    show["Demand adj."] = show.demand_adjust.map(lambda x: "" if abs(x - 1) < 1e-9 else f"{x - 1:+.0%}")
    table = show[["Status", "product_name", "Demand adj.", "on_hand_units", "reorder_point", "Days of stock left",
                  "lead_time_days", "Actual lead time (days)", "suggested_order_units", "Order value",
                  "supplier_name"]].rename(columns={
        "product_name": "Product", "on_hand_units": "In stock", "reorder_point": "Reorder at",
        "lead_time_days": "Promised lead time (days)", "suggested_order_units": "Order qty",
        "supplier_name": "Supplier"})
    only_action = st.toggle("Show only products that need action", value=True)
    if only_action:
        table = table[~table.Status.str.contains("OK|Make to order")]
    if not adjustments:
        table = table.drop(columns="Demand adj.")
    st.dataframe(table, hide_index=True, width="stretch",
                 column_config={"Reorder at": st.column_config.NumberColumn(format="%d")})
    st.download_button("Download order list (Excel/CSV)",
                       order_now[["product_name", "supplier_name", "suggested_order_units",
                                  "suggested_order_value_inr"]].to_csv(index=False).encode("utf-8"),
                       "reorder_list.csv", "text/csv")
    with st.expander("How the reorder point is worked out"):
        st.markdown(f"""
- **Average demand** = units sold per day over the last {cfg.DEMAND_WINDOW_DAYS} days.
- **Lead time** = how long the supplier *actually* took on past orders, not what they promised.
- **Safety stock** = Z × √(lead time × demand variation² + demand² × lead time variation²). It covers busy weeks *and* late suppliers. Z = {cfg.SERVICE_LEVEL_Z} gives about 95% protection per order cycle.
- **Reorder point** = average daily demand × actual lead time + safety stock.
- **Order quantity (EOQ)** = √(2 × yearly demand × {m(supply.order_cost())} per order ÷ yearly holding cost per unit), with holding cost at {cfg.HOLDING_RATE:.0%} of the item's value a year.
- Products selling under {cfg.MAKE_TO_ORDER_BELOW} unit a month (big machines) are **made to order**, not stocked.
- **Overstock** = more than {cfg.OVERSTOCK_MONTHS} months of stock on the shelf.
""")

# ---------------------------------------------------------------------------
# 2. What sells
# ---------------------------------------------------------------------------
with tab2:
    tot = monthly.groupby("month", as_index=False).revenue_inr.sum()
    tot = tot[tot.month < today.to_period("M").to_timestamp()]  # full months only
    st.subheader("Sales each month")
    fig = go.Figure(go.Bar(
        x=tot.month, y=scaled(tot.revenue_inr), marker_color=BLUE,
        marker=dict(cornerradius=4),
        customdata=[[money(v)] for v in tot.revenue_inr],
        hovertemplate="%{x|%b %Y}<br>%{customdata[0]}<extra></extra>"))
    fig.update_yaxes(title=SCALE_LABEL)
    fig.update_xaxes(dtick="M2", tickformat="%b %y")
    st.plotly_chart(style_fig(fig), width="stretch")
    best, worst = tot.loc[tot.revenue_inr.idxmax()], tot.loc[tot.revenue_inr.idxmin()]
    st.caption(f"Best month: {best.month:%b %Y} ({m(best.revenue_inr)}). "
               f"Slowest: {worst.month:%b %Y} ({m(worst.revenue_inr)}). Hover a bar for exact figures.")

    st.subheader("Which products bring in the money (last 12 months)")
    a_share = abc.loc[abc.abc_class == "A", "revenue_inr"].sum() / rev12
    n_a = (abc.abc_class == "A").sum()
    st.write(f"**{n_a} of {len(abc)} products** bring in **{a_share:.0%}** of sales. "
             "Keep these in stock first.")
    colours = {"A": BLUE, "B": ORANGE, "C": AQUA}
    top = abc.sort_values("revenue_inr")
    fig = go.Figure(go.Bar(
        y=top.product_name, x=scaled(top.revenue_inr), orientation="h",
        marker=dict(color=top.abc_class.map(colours), cornerradius=4),
        text=top.abc_class, textposition="outside", textfont=dict(color=INK2),
        customdata=[[money(v), c] for v, c in zip(top.revenue_inr, top.abc_class)],
        hovertemplate="%{y}<br>%{customdata[0]}<br>Class %{customdata[1]}<extra></extra>"))
    fig.update_xaxes(title=SCALE_LABEL)
    st.plotly_chart(style_fig(fig, height=620), width="stretch")
    st.caption("A = top products making up 80% of sales · B = next 15% · C = last 5%")

# ---------------------------------------------------------------------------
# 3. Forecast
# ---------------------------------------------------------------------------
with tab3:
    st.subheader(f"Expected demand in {fc.target_month.iloc[0]}")
    if acc["wape"] is None:
        st.write("Based on the last 3 months. There isn't enough history yet to test its accuracy.")
    else:
        st.write(f"Based on the last 3 months, adjusted for the time of year (when there is a year of history). "
                 f"Tested on the last {acc['months_tested']} months, it was off by **{acc['wape']:.0%}** "
                 f"on average, versus {acc['naive_wape']:.0%} for simply repeating last month.")
    f = fc.copy()
    f["Enough stock?"] = (f.on_hand_units >= f.forecast_units).map({True: "🟢 Yes", False: "🔴 No"})
    f["Your adjustment"] = f.adjustment.map(lambda x: "" if abs(x - 1) < 1e-9 else f"{x - 1:+.0%}")
    cols = ["product_name", "last_3_months_avg", "history_forecast", "Your adjustment", "forecast_units",
            "on_hand_units", "Enough stock?"]
    if not (f.adjustment != 1).any():
        cols = [c for c in cols if c not in ("history_forecast", "Your adjustment")]
    st.dataframe(f[cols].rename(columns={"product_name": "Product", "last_3_months_avg": "Avg last 3 months",
                                         "history_forecast": "From history", "forecast_units": "Forecast (units)",
                                         "on_hand_units": "In stock"}),
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# 3b. Planner demand adjustments
# ---------------------------------------------------------------------------
with tab_adj:
    st.subheader("Tell the forecast what history can't know")
    st.write("A trade show, a new contract, a price rise, a festival shutdown: you know these before the sales data "
             "does. Add them here. An adjustment changes **next month's forecast** if it covers next month, and a "
             "product's **reorder point and safety stock** if it falls before a new order would arrive plus one "
             "more month. Adjustments that overlap multiply (+30% and −10% give +17%).")
    prod_opts = [adjust.ALL] + [f"{p} · {n}" for p, n in zip(reorder.product_id, reorder.product_name)]
    first = today.to_period("M").to_timestamp()
    month_opts = [(first + pd.DateOffset(months=i)).strftime("%b %Y") for i in range(13)]
    ADJ_COLS = ["product", "change_pct", "from_month", "to_month", "reason"]
    if "adj_base" not in st.session_state:
        st.session_state["adj_base"] = pd.DataFrame(columns=ADJ_COLS)
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Load an example", help="A trade-show order for one product and a festival slowdown for all"):
        nxt = month_opts[1] if len(month_opts) > 1 else month_opts[0]
        ex = pd.DataFrame([
            {"product": prod_opts[1], "change_pct": 30, "from_month": month_opts[0], "to_month": nxt,
             "reason": "Big order expected from a new steel-plant customer"},
            {"product": adjust.ALL, "change_pct": -15, "from_month": nxt, "to_month": nxt,
             "reason": "Festival week: customers' plants shut"}], columns=ADJ_COLS)
        st.session_state.update(adj_base=ex, adj_rows=ex.to_dict("records"))
        st.session_state.pop("adj_editor", None)
        st.rerun()
    if b2.button("Clear all"):
        st.session_state.update(adj_base=pd.DataFrame(columns=ADJ_COLS), adj_rows=[])
        st.session_state.pop("adj_editor", None)
        st.rerun()
    edited = st.data_editor(
        st.session_state["adj_base"], num_rows="dynamic", width="stretch", key="adj_editor", hide_index=True,
        column_config={
            "product": st.column_config.SelectboxColumn("Product", options=prod_opts, required=True, width="large"),
            "change_pct": st.column_config.NumberColumn("Change %", min_value=-100, max_value=500, step=5,
                                                        format="%+d%%", required=True),
            "from_month": st.column_config.SelectboxColumn("From", options=month_opts, required=True),
            "to_month": st.column_config.SelectboxColumn("To", options=month_opts),
            "reason": st.column_config.TextColumn("Reason (for your records)", width="large")})
    pending = adjust.clean(edited.to_dict("records")) != adjustments
    if st.button("Apply adjustments", type="primary", disabled=not pending):
        st.session_state["adj_rows"] = edited.to_dict("records")
        st.rerun()
    if pending:
        st.caption("Changes not applied yet. Click **Apply adjustments** to update the forecast and reorder points.")
    if adjustments:
        base = load(db_path, data_ccy, ())[3].set_index("product_id")
        now_ = reorder.set_index("product_id")
        hit = now_[now_.demand_adjust != 1]
        if len(hit):
            st.markdown(f"**Effect on reorder planning ({len(hit)} products)**")
            eff = pd.DataFrame({
                "Product": hit.product_name, "Demand": hit.demand_adjust.map(lambda x: f"{x - 1:+.0%}"),
                "Reorder at (history only)": base.loc[hit.index, "reorder_point"].astype(int),
                "Reorder at (adjusted)": hit.reorder_point.astype(int),
                "Status (history only)": base.loc[hit.index, "status"].map(STATUS_ICON),
                "Status (adjusted)": hit.status.map(STATUS_ICON)})
            st.dataframe(eff, hide_index=True, width="stretch")
        else:
            st.caption("None of the adjustments falls inside a product's reorder window yet, so reorder points "
                       "are unchanged. They may still change next month's forecast.")

# ---------------------------------------------------------------------------
# 3c. Customers
# ---------------------------------------------------------------------------
CUST_ICON = {"Active": "🟢 Active", "New": "🔵 New", "Slowing": "🟠 Slowing", "Gone quiet": "🔴 Gone quiet"}
with tab_cust:
    cs, conc, mix, risk = customer_data(db_path, data_ccy)
    if cs.empty or conc["customers"] == 0:
        st.info("No customer IDs in the sales data, so there's nothing to show here. Add a customer column "
                "(ID or name) to your sales to see who buys what.")
    else:
        top = cs.iloc[0]
        st.subheader(f"{conc['n_for_80']} of {conc['customers']} customers bring in 80% of sales")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Customers, last 12 months", f"{conc['customers']:,}")
        c2.metric("Biggest customer", f"{conc['top1_share']:.0%}")
        c2.caption(str(top.customer_name))
        c3.metric("Top 10 customers", f"{conc['top10_share']:.0%}")
        quiet = cs[cs.status.isin(["Gone quiet", "Slowing"])]
        c4.metric("Gone quiet or slowing", f"{len(quiet)}")
        if conc["top1_share"] >= 0.25:
            st.warning(f"**{top.customer_name} alone is {conc['top1_share']:.0%} of sales.** That's a big dependency: "
                       "if they cut back, demand for the products they buy drops sharply. Keep in close touch, "
                       "agree forecasts with them, and grow other customers.")

        st.markdown("**Customers to call this week**")
        if len(quiet):
            q = quiet.assign(**{
                "Customer": quiet.customer_name, "Status": quiet.status.map(CUST_ICON),
                "Last order": quiet.last_order.dt.strftime("%d %b %Y"), "Days since": quiet.days_since.astype(int),
                "Usually orders every": quiet.usual_gap.round(0).map(lambda x: f"{x:.0f} days" if x == x else ""),
                "Sales, last 3 months": quiet.revenue_last_3m.map(money),
                "Sales, 3 months before": quiet.revenue_prior_3m.map(money)})
            st.dataframe(q[["Customer", "Status", "Last order", "Days since", "Usually orders every",
                            "Sales, last 3 months", "Sales, 3 months before"]], hide_index=True, width="stretch")
            st.caption(f"Gone quiet = a regular customer silent for {cust.QUIET_FACTOR:g} times their usual gap "
                       "between orders. Slowing = a sizeable, frequent customer whose last 3 months are under half "
                       "of the 3 months before.")
        else:
            st.write("Every regular customer is ordering at their usual pace.")

        stocked_ids = set(reorder.loc[~reorder.status.isin(["Not selling"]), "product_id"])
        risk_s = risk[risk.product_id.isin(stocked_ids)] if len(risk) else risk
        if len(risk_s):
            st.markdown("**Stock at risk:** products where those customers took a big share of demand. "
                        "Order less of these until you know what's happening.")
            st.dataframe(risk_s.assign(**{"Share of demand": risk_s.share_at_risk.map(lambda x: f"{x:.0%}")})[
                ["product_name", "Share of demand", "customers"]].rename(columns={
                    "product_name": "Product", "customers": "From customers"}), hide_index=True, width="stretch")

        dep = mix.sort_values("share_of_product", ascending=False).drop_duplicates("product_id")
        dep = dep[(dep.share_of_product >= 0.5) & dep.product_id.isin(stocked_ids)]
        if len(dep):
            names_ = cs.set_index("customer_id")["customer_name"]
            st.markdown("**Products that depend on one customer** (half or more of their demand)")
            st.dataframe(pd.DataFrame({"Product": dep.product_name, "Customer": dep.customer_id.map(names_),
                                       "Share of the product's demand": dep.share_of_product.map(lambda x: f"{x:.0%}")}),
                         hide_index=True, width="stretch")

        st.markdown("**All customers**")
        allv = cs.assign(**{"Customer": cs.customer_name, "Segment": cs.segment,
                            "Sales (12 mo)": cs.revenue_12m.map(money), "Share": cs.share.map(lambda x: f"{x:.1%}"),
                            "Orders (12 mo)": cs.orders_12m.astype(int),
                            "Last order": cs.last_order.dt.strftime("%d %b %Y"), "Status": cs.status.map(CUST_ICON)})
        cols_ = ["Customer", "Segment", "Sales (12 mo)", "Share", "Orders (12 mo)", "Last order", "Status"]
        if not cs.segment.astype(str).str.strip().any():
            cols_.remove("Segment")
        st.dataframe(allv[cols_], hide_index=True, width="stretch", height=320)

        if cs.segment.astype(str).str.strip().any():
            seg = cs.groupby("segment")["revenue_12m"].sum().sort_values()
            fig = go.Figure(go.Bar(y=seg.index, x=[cfg.convert(v, data_ccy, SHOW) / SCALE for v in seg.values],
                                   orientation="h", marker=dict(color=BLUE, cornerradius=4),
                                   customdata=[[money(v)] for v in seg.values],
                                   hovertemplate="%{y}<br>%{customdata[0]}<extra></extra>"))
            fig.update_xaxes(title=f"Sales, last 12 months ({SCALE_LABEL})")
            st.markdown("**Sales by customer type**")
            st.plotly_chart(style_fig(fig, 260), width="stretch")

        st.markdown("**What does one customer buy?**")
        who = st.selectbox("Customer", cs.customer_name.tolist(), key="cust_pick")
        cid = cs.loc[cs.customer_name == who, "customer_id"].iloc[0]
        mine = mix[mix.customer_id == cid].sort_values("revenue", ascending=False)
        if len(mine):
            st.dataframe(pd.DataFrame({"Product": mine.product_name, "Units (12 mo)": mine.units.astype(int),
                                       "Sales (12 mo)": mine.revenue.map(money),
                                       "Their share of this product's demand": mine.share_of_product.map(lambda x: f"{x:.0%}")}),
                         hide_index=True, width="stretch")
        else:
            st.caption("No purchases in the last 12 months.")

# ---------------------------------------------------------------------------
# 4. Stuck stock
# ---------------------------------------------------------------------------
with tab4:
    over = reorder[reorder.status == "Overstock"]
    st.subheader(f"{m(stuck)} tied up in stock that isn't moving")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**No sale in {cfg.IDLE_DAYS}+ days** · {m(idle.stock_value_inr.sum())}")
        st.dataframe(idle.assign(Value=idle.stock_value_inr.apply(money))[
            ["product_name", "on_hand_units", "Value", "days_since_last_sale"]].rename(columns={
                "product_name": "Product", "on_hand_units": "In stock",
                "days_since_last_sale": "Days since last sale"}),
            hide_index=True, width="stretch")
        st.caption("Ideas: offer to regular customers at a discount, bundle with yokes, or stop making.")
    with c2:
        st.markdown(f"**More than {cfg.OVERSTOCK_MONTHS} months of stock** · "
                    f"{m(over.stock_value_inr.sum())}")
        st.dataframe(over.assign(Value=over.stock_value_inr.apply(money),
                                 Months=(over.days_of_cover / 30).round(0).astype(int))[
            ["product_name", "on_hand_units", "Months", "Value"]].rename(columns={
                "product_name": "Product", "on_hand_units": "In stock", "Months": "Months of stock"}),
            hide_index=True, width="stretch")
        st.caption("Pause production of these until stock comes down.")

# ---------------------------------------------------------------------------
# 5. Suppliers
# ---------------------------------------------------------------------------
with tab5:
    st.subheader("Which suppliers can we rely on?")
    if suppliers.empty:
        st.info("No purchase history, so suppliers can't be scored. Add a **purchases** sheet with the "
                "order date and receipt date of each delivery. Until then, reorder points use the "
                "promised lead times.")
    else:
        worst = suppliers.iloc[0]
        st.write(f"**{worst.supplier_name}** delivers on time only **{worst.on_time_pct:.0f}%** of the time, "
                 f"averaging {worst.avg_days_late:.1f} days late. Its products need extra safety stock.")
    st.dataframe(suppliers.drop(columns="supplier_id").rename(columns={
        "supplier_name": "Supplier", "deliveries": "Deliveries", "avg_promised_days": "Promised (days)",
        "avg_actual_days": "Actual (days)", "avg_days_late": "Avg days late", "on_time_pct": "On time %"}),
        hide_index=True, width="stretch",
        column_config={"On time %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100)})
    st.caption(f"On time = delivered no more than {cfg.ON_TIME_GRACE_DAYS} days after the promised lead time.")

    st.subheader("Should we switch supplier?")
    st.write("The cheapest quote isn't always the cheapest supplier. A late or erratic supplier forces you to hold "
             "more safety stock, and that stock costs money every year. So we compare **total yearly cost**: "
             "purchases + ordering + average stock between deliveries + safety stock, with lead times taken from "
             "each supplier's real delivery record, not its promise.")
    if db_path is None:
        sw = switch_summary(db_path, data_ccy, adjustments)
        if len(sw):
            v = pd.DataFrame({
                "Product": sw["product"], "Alternative quote": sw["alternative"],
                "Price": sw["price_change"].map(lambda x: f"{x:+.0%}"),
                "Lead time": sw["lead_time_change"].map(lambda x: f"{x:+.0f} days"),
                "Yearly saving if we switch": sw["yearly_saving"].map(money),
                "Verdict": sw["verdict"].map({"Switch": "✅ Switch", "Stay": "✋ Stay"}),
                "The quote": sw["note"]})
            st.dataframe(v, hide_index=True, width="stretch")
            st.caption("A negative saving means switching would cost more. Price usually dominates; reliability "
                       "wins when the price gap is small, demand is high, or the current supplier is very erratic.")

    st.markdown("**Try a quote from any supplier**")
    stocked = reorder[~reorder.status.isin(["Not selling"])]
    rel_opts = {"unknown": "No history: assume an average supplier (15% late, ±25%)",
                "on_time": "Trust the promise: always on time"}
    if not suppliers.empty:
        rel_opts.update({r.supplier_id: f"As reliable as {r.supplier_name} ({r.on_time_pct:.0f}% on time)"
                         for r in suppliers.itertuples()})
    with st.form("quote"):
        q1, q2 = st.columns(2)
        pick = q1.selectbox("Product", [f"{p} · {n}" for p, n in zip(stocked.product_id, stocked.product_name)])
        name = q2.text_input("Supplier name", "New supplier")
        q3, q4, q5 = st.columns([1, 1, 2])
        cur = stocked.set_index("product_id").loc[pick.split(" · ")[0]]
        quote_price = q3.number_input(f"Unit price ({cfg.fmt(0, SHOW)[0]})",
                                value=float(round(cfg.convert(cur.unit_cost_inr, data_ccy, SHOW) * 0.95, 2)),
                                min_value=0.0, step=1.0)
        lt = q4.number_input("Promised lead time (days)", value=int(cur.lead_time_days), min_value=1, step=1)
        rel = q5.selectbox("How reliable will they be?", list(rel_opts), format_func=rel_opts.get)
        go_ = st.form_submit_button("Compare with current supplier", type="primary")
    if go_:
        an.use_db(db_path, data_ccy)
        t = supply.compare_suppliers(pick.split(" · ")[0], [{
            "supplier_name": name, "unit_cost": cfg.convert(quote_price, SHOW, data_ccy),
            "promised_lead_time_days": lt, "reliability": rel}], adjustments=adjustments)
        view = pd.DataFrame({
            "Supplier": t.supplier, "Unit price": t.unit_cost.map(price),
            "Lead time (expected ± spread)": [f"{a:.0f} ± {b:.0f} days" for a, b in zip(t.expected_days, t.spread_days)],
            "Safety stock": t.safety_stock_units.astype(int), "Purchases / yr": t.purchases.map(money),
            "Ordering / yr": t.ordering.map(money), "Stock between deliveries / yr": t.cycle_stock.map(money),
            "Safety stock / yr": t.safety_stock_cost.map(money), "Total / yr": t.total.map(money)})
        st.dataframe(view, hide_index=True, width="stretch")
        d_total = t.total.iloc[1] - t.total.iloc[0]
        d_price = t.purchases.iloc[1] - t.purchases.iloc[0]
        d_safety = t.safety_stock_cost.iloc[1] - t.safety_stock_cost.iloc[0]
        verdict = "cheaper" if d_total < 0 else "more expensive"
        word = lambda x: "saves" if x < 0 else "adds"  # noqa: E731
        st.markdown(f"**{name} is {m(abs(d_total))} a year {verdict}** in total. Its price {word(d_price)} "
                    f"{m(abs(d_price))} a year; safety stock {word(d_safety)} {m(abs(d_safety))}, because its lead "
                    f"time is estimated at {t.expected_days.iloc[1]:.0f} ± {t.spread_days.iloc[1]:.0f} days "
                    f"({t.basis.iloc[1]}) against {t.expected_days.iloc[0]:.0f} ± {t.spread_days.iloc[0]:.0f} now. "
                    f"**{'Worth switching' if d_total < 0 else 'Stay with the current supplier'}** on cost alone.")
        st.caption("Also weigh what the numbers don't show: quality, payment terms, and the risk of relying "
                   "on a single supplier.")

    st.subheader("How fast does stock turn into sales?")
    all_row = turns[turns.category == "All products"].iloc[0]
    st.write(f"Overall, stock turns over **{all_row.turnover:.1f} times a year**, about "
             f"**{all_row.days_of_inventory:.0f} days** of inventory on the shelf.")
    st.dataframe(turns.assign(
        **{"Cost of goods sold (12 mo)": turns.annual_cogs_inr.apply(money),
           "Stock value": turns.stock_value_inr.apply(money)})[
        ["category", "Cost of goods sold (12 mo)", "Stock value", "turnover", "days_of_inventory"]].rename(columns={
            "category": "Category", "turnover": "Turns a year", "days_of_inventory": "Days of inventory"}),
        hide_index=True, width="stretch",
        column_config={"Turns a year": st.column_config.NumberColumn(format="%.1f"),
                       "Days of inventory": st.column_config.NumberColumn(format="%.0f")})
    st.caption("Turnover = cost of goods sold in 12 months ÷ value of stock on hand today.")

# ---------------------------------------------------------------------------
# 6. Service level what-if
# ---------------------------------------------------------------------------
with tab6:
    st.subheader("How much stock does it cost to avoid running out?")
    st.write("Each row simulates a full year of daily orders, 300 times, using real demand patterns and "
             "real supplier delays from the data. A higher target means fewer stockouts but more money "
             "tied up in stock.")
    sim_key = f"sim_{db_path}"
    if uploaded and not st.session_state.get(sim_key):
        st.info(f"This runs 1,200 simulated years for your {len(reorder)} products and can take up to a minute.")
        if st.button("Run the simulation", type="primary"):
            st.session_state[sim_key] = True
            st.rerun()
    else:
        sim = what_if(db_path, data_ccy)
        view = pd.DataFrame({
            "Target": (sim.target_service_level * 100).round(0).astype(int).astype(str) + "%",
            "Demand met from stock": (sim.fill_rate * 100).round(1).astype(str) + "%",
            "Stockout days per product per year": sim.stockout_days_per_product.round(1),
            "Average stock value": sim.avg_stock_value_inr.apply(money),
            "Yearly holding cost": sim.yearly_holding_cost_inr.apply(money),
        })
        st.dataframe(view, hide_index=True, width="stretch")
        lo, hi = sim.iloc[1], sim.iloc[-1]
        extra = hi.avg_stock_value_inr - lo.avg_stock_value_inr
        st.write(f"Going from **{lo.target_service_level:.0%} to {hi.target_service_level:.0%}** adds "
                 f"**{m(extra)}** of average stock ({m(extra * cfg.HOLDING_RATE)} a year to hold) "
                 f"and lifts demand met from stock by **{(hi.fill_rate - lo.fill_rate) * 100:.1f} points**.")
        fig = go.Figure(go.Scatter(
            x=scaled(sim.avg_stock_value_inr), y=sim.fill_rate * 100, mode="lines+markers+text",
            text=[f"{v:.0%}" for v in sim.target_service_level], textposition="top left",
            line=dict(color=BLUE, width=2), marker=dict(size=10, color=BLUE),
            hovertemplate=f"Stock %{{x:.1f}} ({SCALE_LABEL})<br>Demand met %{{y:.1f}}%<extra></extra>"))
        fig.update_xaxes(title=f"Average stock value ({SCALE_LABEL})")
        fig.update_yaxes(title="Demand met from stock (%)",
                         range=[sim.fill_rate.min() * 100 - 0.4, sim.fill_rate.max() * 100 + 0.4])
        st.plotly_chart(style_fig(fig, 320), width="stretch")
        st.caption("Targets are per order cycle. Demand met counts units (weighted by value), so it runs higher "
                   "than the target. Unmet demand is treated as a lost sale.")

# ---------------------------------------------------------------------------
# 7. Real data case study (UCI Online Retail II)
# ---------------------------------------------------------------------------
@st.cache_data
def real():
    return rd.load_results()


def gbp(v):
    """UK amounts are in pounds; shown in US dollars at a fixed 2010-2011 rate."""
    return f"${v * cfg.USD_PER_GBP:,.0f}"


def gbp_md(v):
    """The same, for Markdown text (escaped '$')."""
    return gbp(v).replace("$", "\\$")


with tab7:
    st.subheader("The same methods on real data: a UK online gift wholesaler")
    st.write("Real transactions from 2 years (Dec 2009 to Dec 2011) of a UK online wholesaler selling gifts "
             "and homeware, mostly to other businesses. Public dataset: *Online Retail II*, UCI Machine "
             "Learning Repository. Amounts converted from pounds to US dollars at £1 = \\$"
             f"{cfg.USD_PER_GBP}, roughly the 2010 to 2011 average.")
    if not rd.results_available():
        st.warning("The real dataset isn't loaded yet. Run `python src/real_data.py` once "
                   "(downloads about 45 MB and takes 2 to 3 minutes).")
    else:
        log, ra, mx, rm, rret, bt = real()
        raw_rows = int(log.loc[0, "why"].split()[0].replace(",", ""))
        kept = raw_rows - int(log.rows_removed.sum())
        a_n = int((ra.abc_class == "A").sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Invoice lines", f"{raw_rows:,}")
        c2.metric("Kept after cleaning", f"{kept:,}")
        c2.caption(f"{kept / raw_rows:.1%} of lines")
        c3.metric("Sales, last 12 months", gbp(ra.revenue_gbp.sum()))
        c4.metric("Products making 80% of sales", f"{a_n:,} of {len(ra):,}")

        st.markdown("#### 1. Cleaning")
        st.write("Every row removed has a reason. Cancellations are not thrown away: they become the returns data below.")
        st.dataframe(log.assign(**{"Value removed": log.value_removed_gbp.apply(lambda v: gbp(v) if v else "")})[
            ["step", "rows_removed", "Value removed", "why"]].rename(columns={
                "step": "Step", "rows_removed": "Rows removed", "why": "Why"}),
            hide_index=True, width="stretch",
            column_config={"Rows removed": st.column_config.NumberColumn(format="%d")})

        st.markdown("#### 2. Sales each month")
        full = rm[rm.month < "2011-12-01"]
        fig = go.Figure(go.Bar(x=full.month, y=full.revenue_gbp * cfg.USD_PER_GBP / 1e3, marker=dict(color=BLUE, cornerradius=4),
                               hovertemplate="%{x|%b %Y}<br>$%{y:,.0f}k<extra></extra>"))
        fig.update_yaxes(title="$ thousand")
        fig.update_xaxes(dtick="M2", tickformat="%b %y")
        st.plotly_chart(style_fig(fig, 300), width="stretch")
        peak = full.loc[full.revenue_gbp.idxmax()]
        st.caption(f"Strong pre-Christmas season: the peak was {peak.month:%B %Y} at {gbp_md(peak.revenue_gbp)}. "
                   "December 2011 is left out because the data stops on the 9th.")

        st.markdown("#### 3. ABC-XYZ: where planning effort pays off")
        st.write("**ABC** ranks products by revenue. **XYZ** ranks them by how steady monthly demand is "
                 "(X steady, Y variable, Z erratic, by coefficient of variation).")
        piv_n = mx.pivot(index="abc_class", columns="xyz_class", values="products").fillna(0).astype(int)
        piv_s = mx.pivot(index="abc_class", columns="xyz_class", values="revenue_share").fillna(0)
        grid = piv_n.astype(str) + " products · " + (piv_s * 100).round(1).astype(str) + "% of sales"
        grid.index = grid.index.map({"A": "A (top 80% of sales)", "B": "B (next 15%)", "C": "C (last 5%)"})
        grid.columns = grid.columns.map({"X": "X steady", "Y": "Y variable", "Z": "Z erratic"})
        grid.index.name = "Value \\ Demand"
        st.dataframe(grid, width="stretch")
        ax = mx[(mx.abc_class == "A") & (mx.xyz_class == "X")].iloc[0]
        cz = mx[(mx.abc_class == "C") & (mx.xyz_class == "Z")].iloc[0]
        st.write(f"**AX ({int(ax.products)} products, {ax.revenue_share:.0%} of sales):** high value and "
                 f"predictable. Forecast tightly and never run out. "
                 f"**CZ ({int(cz.products)} products, {cz.revenue_share:.1%} of sales):** low value and erratic. "
                 "Keep minimal stock or source to order, and review whether to keep them at all.")

        st.markdown("#### 4. Which forecasting method works?")
        st.write(f"Six methods tested on the top {bt['products']} products, forecasting {bt['months']} "
                 "one month at a time using only earlier data.")
        tbl = bt["table"].assign(**{
            "Error per product": (bt["table"].product_wape * 100).round(1).astype(str) + "%",
            "Error on total volume": (bt["table"].total_error * 100).round(1).astype(str) + "%"})
        st.dataframe(tbl[["method", "Error per product", "Error on total volume"]].rename(columns={"method": "Method"}),
                     hide_index=True, width="stretch")
        bp, btot = bt["best_product"], bt["best_total"]
        st.write(f"**Finding:** seasonality helps when forecasting the business as a whole "
                 f"(**{btot.method}**: {btot.total_error:.0%} error) but hurts at product level, where "
                 f"**{bp.method}** is best ({bp.product_wape:.0%}). Plan cash, staff and warehouse space with "
                 "the seasonal total. Plan individual products with the simple average plus safety stock, "
                 "because single-product demand is noisy.")
        mm = bt["monthly"][bt["monthly"].method == btot.method]
        fig = go.Figure()
        fig.add_bar(x=mm.month, y=mm.actual / 1e3, name="Actual", marker=dict(color=BLUE, cornerradius=4),
                    hovertemplate="%{x|%b %Y}<br>Actual %{y:.0f}k units<extra></extra>")
        fig.add_scatter(x=mm.month, y=mm.forecast / 1e3, name="Forecast", mode="lines+markers",
                        line=dict(color=ORANGE, width=2), marker=dict(size=9, color=ORANGE),
                        hovertemplate="%{x|%b %Y}<br>Forecast %{y:.0f}k units<extra></extra>")
        fig.update_yaxes(title="Units (thousand), top products")
        fig.update_xaxes(tickformat="%b %Y")
        fig = style_fig(fig, 300)
        fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.12, x=0))
        st.plotly_chart(fig, width="stretch")

        st.markdown("#### 5. Returns by country")
        top_r = rret.iloc[0]
        st.write(f"Return rate = value cancelled ÷ value sold. **{top_r.country}** stands out at "
                 f"{top_r.return_rate_pct:.0f}%, worth checking with the account manager.")
        st.dataframe(rret.assign(**{"Sold": rret.sold_gbp.apply(gbp), "Returned": rret.returned_gbp.apply(gbp)})[
            ["country", "Sold", "Returned", "return_rate_pct"]].rename(columns={
                "country": "Country", "return_rate_pct": "Return rate %"}), hide_index=True, width="stretch")
