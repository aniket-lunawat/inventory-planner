"""
Dashboard pages for a messy sales export (see raw_import.py for the logic).

render(files, currency) draws: how the file was read (with a fix-it control for every
column guess), what was cleaned and why, and answers from sales alone. Heavy steps
are cached per file, so changing a tab or a setting doesn't redo them.
"""
import gzip
import io

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config as cfg
import raw_import as ri

BLUE, ORANGE, GRID, INK2 = "#2a78d6", "#eb6834", "#e7e6e2", "#52514e"
SYMBOL = {"USD": "$", "INR": "₹", "GBP": "£", "EUR": "€"}
NONE = "(not in my file)"


# ---------------------------------------------------------------------------
# Cached steps (cache_resource: big tables are shared, not copied on every rerun)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="Reading your file...", max_entries=1)
def _read(files):
    return ri.read_any(list(files))


@st.cache_resource(show_spinner="Cleaning: finding duplicates, returns, tax lines and typos...", max_entries=2)
def _clean(files, roles):
    table, _ = _read(files)
    return ri.clean(table, dict(roles))


@st.cache_resource(show_spinner="Working out what sells, what's slowing and what's coming next...", max_entries=6)
def _analyse(files, roles):
    sales, returns, _, _ = _clean(files, roles)
    return ri.analyse(sales, returns)


@st.cache_data(show_spinner="Preparing the download...", max_entries=3)
def _clean_csv(_sales, key):
    out = _sales.assign(date=_sales["date"].dt.date)
    data = out.to_csv(index=False).encode("utf-8")
    return gzip.compress(data) if len(out) > 150_000 else data


# ---------------------------------------------------------------------------
def _money(v, ccy):
    if ccy == "INR":
        return cfg.inr_fmt(v)
    return f"-{SYMBOL[ccy]}{abs(v):,.0f}" if v < 0 else f"{SYMBOL[ccy]}{v:,.0f}"


def _md(text):
    return text.replace("$", "\\$")


def _style(fig, height=320):
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=8, b=8), plot_bgcolor="white", paper_bgcolor="white",
                      font=dict(color=INK2, size=13), showlegend=False, bargap=0.25,
                      hoverlabel=dict(bgcolor="white", font_size=13))
    fig.update_xaxes(showgrid=False, linecolor=GRID)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def roles_key(roles: dict) -> tuple:
    return tuple(sorted((k, v) for k, v in roles.items()))


def column_mapping(files) -> dict | None:
    """Shows how the file was read and lets the user correct any column. Returns roles, or None to stop."""
    table, notes = _read(files)
    if table.empty:
        st.error("**No table found in this file.** Is it a sales export (one row per sale or invoice line)?")
        return None
    guess = ri.guess_roles(table)
    gaps = ri.missing_roles(guess)
    with st.expander(f"How we read your file: {len(table):,} rows, {table.shape[1]} columns. Click to check or fix.",
                     expanded=bool(gaps)):
        for n in notes:
            st.caption(n)
        st.dataframe(table.head(6).astype(str).replace({"nan": "", "None": ""}), hide_index=True, width="stretch")
        st.markdown("**Which column is which?** We guessed from the names and the contents. Change any that's wrong.")
        cols = [NONE] + list(table.columns)
        roles = {}
        grid = st.columns(3)
        for i, role in enumerate(ri.ROLES):
            default = guess.get(role)
            roles[role] = grid[i % 3].selectbox(
                ri.LABELS[role], cols, index=cols.index(default) if default in cols else 0, key=f"role_{role}")
            if roles[role] == NONE:
                roles[role] = None
    gaps = ri.missing_roles(roles)
    if gaps:
        st.error("We need " + " and ".join(gaps) + " to continue. Pick it in **How we read your file** above.")
        return None
    return roles


def render(files, ccy: str):
    files = tuple(files)
    st.title("Inventory Planner")
    st.caption("Your sales export, cleaned and analysed. Nothing is stored after you close the page.")
    roles = column_mapping(files)
    if roles is None:
        st.stop()
    st.session_state["raw_roles"] = roles          # remembered for when the stock sheet is added
    key = roles_key(roles)
    sales, returns, log, fixes = _clean(files, key)
    if sales.empty:
        st.error("**Every row was removed while cleaning.** Check the column choices above, especially date and "
                 "quantity, and the cleaning log below.")
        st.dataframe(log, hide_index=True, width="stretch")
        st.stop()
    a = _analyse(files, key)
    money = a["money"]
    m = lambda v: _money(v, ccy)  # noqa: E731

    # ---- headline -----------------------------------------------------------
    raw_rows = int(log.iloc[0]["Why"].split()[0].replace(",", ""))
    st.success(f"**Cleaned {raw_rows:,} rows into {a['lines']:,} sales lines** for {a['products']:,} products, "
               f"{a['start']:%d %b %Y} to {a['end']:%d %b %Y}.")
    k = st.columns(4)
    if money:
        k[0].metric("Sales, last 12 months", m(a["revenue_12m"]))
    else:
        k[0].metric("Units sold, last 12 months", f"{a['units_12m']:,.0f}")
    a_n = int((a["abc"]["abc"] == "A").sum())
    k[1].metric("Products making 80% of sales", f"{a_n:,} of {len(a['abc']):,}")
    k[2].metric("Products that stopped selling", f"{len(a['stopped']):,}")
    best = a["test"]["table"]
    if len(best):
        k[3].metric("Forecast error, best method", f"{best.iloc[0]['Error per product']:.0%}")
        k[3].caption(f"per product, tested on {a['test']['months']} months")

    tabs = st.tabs(["🧹 Cleaning report", "📈 Sales trend", "🅰️ What sells", "🔮 Next month",
                    "🐢 Slowing & stopped", "↩️ Returns", "👥 Customers", "🔓 Unlock reorder alerts"])

    # ---- cleaning -----------------------------------------------------------
    with tabs[0]:
        st.subheader("What we cleaned, and why")
        st.write("Every removed row has a reason. Nothing is guessed silently.")
        shown = log[(log["Rows removed"] > 0) | log.index.isin([0, len(log) - 1])]   # hide steps that found nothing
        view = shown.assign(**{"Value removed": shown["Value removed"].map(lambda v: m(v) if v else "")})
        st.dataframe(view, hide_index=True, width="stretch",
                     column_config={"Rows removed": st.column_config.NumberColumn(format="%d"),
                                    "Why": st.column_config.TextColumn(width="large")})
        for f in fixes:
            st.markdown(f"- {_md(f)}")
        big = len(sales) > 150_000
        st.download_button(f"Download the cleaned sales ({'.csv.gz' if big else '.csv'})",
                           _clean_csv(sales, key), "cleaned_sales.csv.gz" if big else "cleaned_sales.csv",
                           help="One clean row per sale: date, product, name, quantity, price, revenue, invoice, customer.")

    # ---- trend --------------------------------------------------------------
    with tabs[1]:
        mo = a["monthly"].copy()
        if a["partial_last"] and len(mo) > 1:
            mo = mo.iloc[:-1]
        y = mo["revenue"] if money else mo["units"]
        st.subheader("Sales each month" if money else "Units sold each month")
        fig = go.Figure(go.Bar(x=mo.index, y=y, marker=dict(color=BLUE, cornerradius=4),
                               customdata=[[m(v) if money else f"{v:,.0f} units"] for v in y],
                               hovertemplate="%{x|%b %Y}<br>%{customdata[0]}<extra></extra>"))
        fig.update_xaxes(tickformat="%b %y")
        st.plotly_chart(_style(fig), width="stretch")
        if len(mo) >= 2:
            hi, lo = y.idxmax(), y.idxmin()
            st.caption(_md(f"Best month: {hi:%b %Y} ({m(y[hi]) if money else f'{y[hi]:,.0f} units'}). "
                           f"Slowest: {lo:%b %Y} ({m(y[lo]) if money else f'{y[lo]:,.0f} units'}). "
                           + ("The last, incomplete month is left out." if a["partial_last"] else "")))

    # ---- ABC-XYZ ------------------------------------------------------------
    with tabs[2]:
        by = a["abc"]
        share = by.loc[by["abc"] == "A", "share"].sum()
        st.subheader(f"{a_n:,} of {len(by):,} products bring in {share:.0%} of sales")
        st.write("**ABC** ranks products by sales over the last 12 months (A = top 80%, B = next 15%, C = last 5%). "
                 "**XYZ** says how steady monthly demand is (X steady, Y variable, Z erratic).")
        g = a["grid"]
        if len(g):
            cell = g.assign(txt=g["products"].map("{:,}".format) + " products · " +
                            (g["share"] * 100).round(1).astype(str) + "%")
            piv = cell.pivot(index="abc", columns="xyz", values="txt").reindex(index=["A", "B", "C"],
                                                                              columns=["X", "Y", "Z"]).fillna("")
            piv.index = ["A (top 80%)", "B (next 15%)", "C (last 5%)"]
            piv.columns = ["X steady", "Y variable", "Z erratic"]
            st.dataframe(piv, width="stretch")
            st.caption("AX: never run out, plan tightly. CZ: keep little or no stock, or make/buy to order.")
        top = by.head(50).reset_index()
        cols = {"product": "Code", "name": "Product", "units": "Units (12 mo)", "abc": "ABC", "xyz": "XYZ"}
        if money:
            top["Sales (12 mo)"] = top["revenue"].map(m)
        st.dataframe(top[list(cols) + (["Sales (12 mo)"] if money else [])].rename(columns=cols),
                     hide_index=True, width="stretch")

    # ---- forecast -----------------------------------------------------------
    with tabs[3]:
        t = a["test"]
        if a["next"] is None or not len(t["table"]):
            st.info("Not enough history to forecast yet: it needs at least 4 full months of sales.")
        else:
            st.subheader(f"Expected demand in {a['next'].attrs['month']}")
            st.write(f"We tested {len(t['table'])} methods on your top products, forecasting each of the last "
                     f"{t['months']} months using only the months before it. **{t['best']}** was most accurate, "
                     "so it makes the forecast below.")
            tt = t["table"].assign(**{c: (t["table"][c] * 100).round(1).astype(str) + "%"
                                      for c in ["Error per product", "Error on total"]})
            st.dataframe(tt, hide_index=True, width="stretch")
            nx = a["next"].head(100).rename(columns={"product": "Code", "name": "Product", "forecast": "Forecast (units)",
                                                      "last_3_avg": "Avg last 3 months", "abc": "ABC", "xyz": "XYZ"})
            st.dataframe(nx[["Code", "Product", "Forecast (units)", "Avg last 3 months", "ABC", "XYZ"]],
                         hide_index=True, width="stretch")

    # ---- slowing / stopped --------------------------------------------------
    with tabs[4]:
        st.subheader("Products that stopped or are slowing down")
        stp = a["stopped"]
        st.markdown(f"**{len(stp):,} {'product hasn' if len(stp) == 1 else 'products haven'}'t sold in 90+ days** "
                    "but used to sell. "
                    "If you still hold them, that stock is at risk: discount, bundle or return it.")
        if len(stp):
            v = stp.head(100).reset_index().assign(**{"Last sale": lambda x: x["last_sale"].dt.strftime("%d %b %Y")})
            cols = {"product": "Code", "name": "Product", "Last sale": "Last sale", "days_since": "Days since",
                    "units": "Units sold (all time)"}
            st.dataframe(v[list(cols)].rename(columns=cols), hide_index=True, width="stretch")
        sl = a["slowing"]
        if len(sl):
            st.markdown(f"**{len(sl):,} product{'' if len(sl) == 1 else 's'} selling at least 50% less** "
                        "in the last 3 months than the 3 before.")
            v = sl.head(100).reset_index().assign(Change=lambda x: (x["change"] * 100).round(0).astype(int).astype(str) + "%")
            cols = {"product": "Code", "name": "Product", "prior_3m": "Units/month before", "last_3m": "Units/month now",
                    "Change": "Change", "abc": "ABC"}
            st.dataframe(v[list(cols)].rename(columns=cols), hide_index=True, width="stretch")

    # ---- returns ------------------------------------------------------------
    with tabs[5]:
        r = a["returns"]
        if r is None or not len(r):
            st.info("No returns, credit notes or cancellations found in the file.")
        else:
            st.subheader("What comes back")
            tot_ret = r["returned_units"].sum()
            st.write(f"{tot_ret:,.0f} units were returned or cancelled, against {a['units_12m']:,.0f} sold in the last "
                     "12 months. Products with a high return rate point to quality, description or packing problems.")
            v = r.head(50).reset_index().assign(Rate=lambda x: (x["rate"] * 100).round(1).astype(str) + "%")
            cols = {"product": "Code", "name": "Product", "returned_units": "Units returned", "sold_units": "Units sold",
                    "Rate": "Return rate"}
            if money:
                v["Value returned"] = v["returned_value"].map(m)
            st.dataframe(v[list(cols) + (["Value returned"] if money else [])].rename(columns=cols),
                         hide_index=True, width="stretch")

    # ---- customers ----------------------------------------------------------
    with tabs[6]:
        c = a["customers"]
        if not c:
            st.info("No customer column found, or it's mostly empty.")
        else:
            st.subheader(f"Top 10 customers bring in {c['top10_share']:.0%} of sales")
            st.write(f"{c['customers']:,} customers bought in the last 12 months. A high share from a few customers is "
                     "a risk: losing one hurts. Plan stock for their orders first.")
            top = c["top"].reset_index()
            top.columns = ["Customer", "Sales (12 mo)" if money else "Units (12 mo)"]
            if money:
                top[top.columns[1]] = top[top.columns[1]].map(m).astype(object)
            st.dataframe(top, hide_index=True, width="stretch")

    # ---- unlock -------------------------------------------------------------
    with tabs[7]:
        st.subheader("Get reorder alerts, safety stock and order quantities")
        st.write("A sales export can't say what's on your shelf or how long suppliers take, and those decide when to "
                 "reorder. We've listed your top products by sales. Add three numbers per product and upload it back.")
        st.markdown("1. Download the sheet below. It's already filled with your top 300 products.\n"
                    "2. Fill **on_hand_units**, and if you know them, **unit_cost** and **lead_time_days**.\n"
                    "3. Upload it in the sidebar under **Add stock (optional)**. Every planning tab opens for those products.")
        st.download_button("Download my stock sheet (.xlsx)", ri.stock_sheet(sales, a), "my_stock_sheet.xlsx",
                           type="primary")
    return sales


def unlocked_tables(files, stock_files):
    """Clean sales + the filled stock sheet -> raw tables for upload.validate(), with notes."""
    files = tuple(files)
    table, _ = _read(files)
    roles = st.session_state.get("raw_roles") or ri.guess_roles(table)
    if ri.missing_roles(roles):
        roles = ri.guess_roles(table)
    sales, returns, _, _ = _clean(files, roles_key(roles))
    stock = ri.read_stock_sheet(list(stock_files))
    if stock.empty or "product_id" not in stock:
        return None, ["The stock file needs a product_id column. Use the sheet from the 'Unlock reorder alerts' tab."]
    return ri.to_tables(sales, stock, sales["date"].max())
