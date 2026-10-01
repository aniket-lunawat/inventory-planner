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

st.set_page_config(page_title="Inventory Planner", page_icon="📦", layout="wide")

# Colours (validated chart palette + status colours)
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
GRID, INK2 = "#e7e6e2", "#52514e"
STATUS_ICON = {"Order now": "🔴 Order now", "Overstock": "🟠 Overstock",
               "Not selling": "⚫ Not selling", "Make to order": "🔵 Make to order", "OK": "🟢 OK"}


# ---------------------------------------------------------------------------
# Data (cached so the page is fast)
# ---------------------------------------------------------------------------
@st.cache_data
def load():
    if not cfg.DB_PATH.exists():
        import load_db
        load_db.main()
    fc, acc = an.forecast()
    return (an.abc(), an.monthly_sales(), an.idle_stock(), an.reorder_table(), fc, acc, an.today(),
            supply.supplier_scorecard(), supply.turnover())


@st.cache_data
def what_if():
    return supply.simulate_service_levels()


abc, monthly, idle, reorder, fc, acc, today, suppliers, turns = load()
using_real = all((cfg.RAW_DIR / f"{t}.csv").exists() for t in ["sales", "products", "stock"])


def usd(inr):
    return f"≈\\${inr / cfg.INR_PER_USD:,.0f}"


def m(inr):
    """Money for text shown as Markdown: '$' is escaped, or two of them would render as maths."""
    return cfg.money(inr).replace("$", "\\$")


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
st.caption(f"Stock as of {today:%d %b %Y}. Money in rupees, with US dollars at ₹{cfg.INR_PER_USD:.0f} = $1.")
if not using_real:
    st.info("**Simulated data.** A made-up company modeled on a small Indian manufacturer of magnetic "
            "inspection tools: 24 products, 40 customers, 2 years of orders. No real company figures are shown.")

order_now = reorder[reorder.status == "Order now"]
stuck = idle.stock_value_inr.sum() + reorder.loc[reorder.status == "Overstock", "stock_value_inr"].sum()
rev12 = abc.revenue_inr.sum()

k1, k2, k3, k4 = st.columns(4)
with k1:
    st.metric("Sales, last 12 months", cfg.inr_fmt(rev12))
    st.caption(usd(rev12))
with k2:
    st.metric("Products to reorder now", f"{len(order_now)}")
    st.caption(f"Order worth {m(order_now.suggested_order_value_inr.sum())}")
with k3:
    st.metric("Money stuck in slow stock", cfg.inr_fmt(stuck))
    st.caption(f"{usd(stuck)} · idle or more than {cfg.OVERSTOCK_MONTHS} months of stock")
with k4:
    st.metric(f"Forecast accuracy", f"{1 - acc['wape']:.0%}")
    st.caption(f"Tested on the last 6 months (simple guess: {1 - acc['naive_wape']:.0%})")

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "🔴 Reorder now", "📈 What sells", "🔮 Next month", "📦 Stuck stock", "🚚 Suppliers",
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
    show["Order value"] = show.suggested_order_value_inr.apply(lambda v: cfg.money(v) if v else "")
    show["suggested_order_units"] = show.suggested_order_units.apply(lambda q: str(q) if q else "")
    show["Actual lead time (days)"] = show.lt_mean.round(0).astype(int)
    table = show[["Status", "product_name", "on_hand_units", "reorder_point", "Days of stock left",
                  "lead_time_days", "Actual lead time (days)", "suggested_order_units", "Order value",
                  "supplier_name"]].rename(columns={
        "product_name": "Product", "on_hand_units": "In stock", "reorder_point": "Reorder at",
        "lead_time_days": "Promised lead time (days)", "suggested_order_units": "Order qty",
        "supplier_name": "Supplier"})
    only_action = st.toggle("Show only products that need action", value=True)
    if only_action:
        table = table[~table.Status.str.contains("OK|Make to order")]
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
- **Order quantity (EOQ)** = √(2 × yearly demand × ₹{cfg.ORDER_COST_INR:,} per order ÷ yearly holding cost per unit), with holding cost at {cfg.HOLDING_RATE:.0%} of the item's value a year.
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
        x=tot.month, y=tot.revenue_inr / 1e5, marker_color=BLUE,
        marker=dict(cornerradius=4),
        customdata=[[cfg.money(v)] for v in tot.revenue_inr],
        hovertemplate="%{x|%b %Y}<br>%{customdata[0]}<extra></extra>"))
    fig.update_yaxes(title="₹ lakh")
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
        y=top.product_name, x=top.revenue_inr / 1e5, orientation="h",
        marker=dict(color=top.abc_class.map(colours), cornerradius=4),
        text=top.abc_class, textposition="outside", textfont=dict(color=INK2),
        customdata=[[cfg.money(v), c] for v, c in zip(top.revenue_inr, top.abc_class)],
        hovertemplate="%{y}<br>%{customdata[0]}<br>Class %{customdata[1]}<extra></extra>"))
    fig.update_xaxes(title="₹ lakh")
    st.plotly_chart(style_fig(fig, height=620), width="stretch")
    st.caption("A = top products making up 80% of sales · B = next 15% · C = last 5%")

# ---------------------------------------------------------------------------
# 3. Forecast
# ---------------------------------------------------------------------------
with tab3:
    st.subheader(f"Expected demand in {fc.target_month.iloc[0]}")
    st.write(f"Based on the last 3 months, adjusted for the time of year. Tested on the last "
             f"6 months, it was off by **{acc['wape']:.0%}** on average, versus "
             f"{acc['naive_wape']:.0%} for simply repeating last month.")
    f = fc.copy()
    f["Enough stock?"] = (f.on_hand_units >= f.forecast_units).map({True: "🟢 Yes", False: "🔴 No"})
    st.dataframe(f[["product_name", "last_3_months_avg", "forecast_units", "on_hand_units", "Enough stock?"]]
                 .rename(columns={"product_name": "Product", "last_3_months_avg": "Avg last 3 months",
                                  "forecast_units": "Forecast (units)", "on_hand_units": "In stock"}),
                 hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# 4. Stuck stock
# ---------------------------------------------------------------------------
with tab4:
    over = reorder[reorder.status == "Overstock"]
    st.subheader(f"{m(stuck)} tied up in stock that isn't moving")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**No sale in {cfg.IDLE_DAYS}+ days** · {m(idle.stock_value_inr.sum())}")
        st.dataframe(idle.assign(Value=idle.stock_value_inr.apply(cfg.money))[
            ["product_name", "on_hand_units", "Value", "days_since_last_sale"]].rename(columns={
                "product_name": "Product", "on_hand_units": "In stock",
                "days_since_last_sale": "Days since last sale"}),
            hide_index=True, width="stretch")
        st.caption("Ideas: offer to regular customers at a discount, bundle with yokes, or stop making.")
    with c2:
        st.markdown(f"**More than {cfg.OVERSTOCK_MONTHS} months of stock** · "
                    f"{m(over.stock_value_inr.sum())}")
        st.dataframe(over.assign(Value=over.stock_value_inr.apply(cfg.money),
                                 Months=(over.days_of_cover / 30).round(0).astype(int))[
            ["product_name", "on_hand_units", "Months", "Value"]].rename(columns={
                "product_name": "Product", "on_hand_units": "In stock", "Months": "Months of stock"}),
            hide_index=True, width="stretch")
        st.caption("Pause production of these until stock comes down.")

# ---------------------------------------------------------------------------
# 5. Suppliers
# ---------------------------------------------------------------------------
with tab5:
    worst = suppliers.iloc[0]
    st.subheader("Which suppliers can we rely on?")
    st.write(f"**{worst.supplier_name}** delivers on time only **{worst.on_time_pct:.0f}%** of the time, "
             f"averaging {worst.avg_days_late:.1f} days late. Its products need extra safety stock.")
    st.dataframe(suppliers.drop(columns="supplier_id").rename(columns={
        "supplier_name": "Supplier", "deliveries": "Deliveries", "avg_promised_days": "Promised (days)",
        "avg_actual_days": "Actual (days)", "avg_days_late": "Avg days late", "on_time_pct": "On time %"}),
        hide_index=True, width="stretch",
        column_config={"On time %": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100)})
    st.caption(f"On time = delivered no more than {cfg.ON_TIME_GRACE_DAYS} days after the promised lead time.")

    st.subheader("How fast does stock turn into sales?")
    all_row = turns[turns.category == "All products"].iloc[0]
    st.write(f"Overall, stock turns over **{all_row.turnover:.1f} times a year**, about "
             f"**{all_row.days_of_inventory:.0f} days** of inventory on the shelf.")
    st.dataframe(turns.assign(
        **{"Cost of goods sold (12 mo)": turns.annual_cogs_inr.apply(cfg.money),
           "Stock value": turns.stock_value_inr.apply(cfg.money)})[
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
    sim = what_if()
    view = pd.DataFrame({
        "Target": (sim.target_service_level * 100).round(0).astype(int).astype(str) + "%",
        "Demand met from stock": (sim.fill_rate * 100).round(1).astype(str) + "%",
        "Stockout days per product per year": sim.stockout_days_per_product.round(1),
        "Average stock value": sim.avg_stock_value_inr.apply(cfg.money),
        "Yearly holding cost": sim.yearly_holding_cost_inr.apply(cfg.money),
    })
    st.dataframe(view, hide_index=True, width="stretch")
    lo, hi = sim.iloc[1], sim.iloc[-1]
    extra = hi.avg_stock_value_inr - lo.avg_stock_value_inr
    st.write(f"Going from **{lo.target_service_level:.0%} to {hi.target_service_level:.0%}** adds "
             f"**{m(extra)}** of average stock ({m(extra * cfg.HOLDING_RATE)} a year to hold) "
             f"and lifts demand met from stock by **{(hi.fill_rate - lo.fill_rate) * 100:.1f} points**.")
    fig = go.Figure(go.Scatter(
        x=sim.avg_stock_value_inr / 1e5, y=sim.fill_rate * 100, mode="lines+markers+text",
        text=[f"{v:.0%}" for v in sim.target_service_level], textposition="top left",
        line=dict(color=BLUE, width=2), marker=dict(size=10, color=BLUE),
        hovertemplate="Stock ₹%{x:.1f} lakh<br>Demand met %{y:.1f}%<extra></extra>"))
    fig.update_xaxes(title="Average stock value (₹ lakh)")
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
    return f"£{v:,.0f}"


with tab7:
    st.subheader("The same methods on real data: a UK online gift wholesaler")
    st.write("Real transactions from 2 years (Dec 2009 to Dec 2011) of a UK online wholesaler selling gifts "
             "and homeware, mostly to other businesses. Public dataset: *Online Retail II*, UCI Machine "
             "Learning Repository. Money in pounds.")
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
        fig = go.Figure(go.Bar(x=full.month, y=full.revenue_gbp / 1e3, marker=dict(color=BLUE, cornerradius=4),
                               hovertemplate="%{x|%b %Y}<br>£%{y:,.0f}k<extra></extra>"))
        fig.update_yaxes(title="£ thousand")
        fig.update_xaxes(dtick="M2", tickformat="%b %y")
        st.plotly_chart(style_fig(fig, 300), width="stretch")
        peak = full.loc[full.revenue_gbp.idxmax()]
        st.caption(f"Strong pre-Christmas season: the peak was {peak.month:%B %Y} at {gbp(peak.revenue_gbp)}. "
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
