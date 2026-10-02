# Inventory Planner

**Live demo:** https://inventory-planner-aniket.streamlit.app

An inventory and demand planning tool built in Python, SQL and Streamlit. It answers the questions an operations or supply chain team asks every week:

1. **What should we reorder now, and how much?** Reorder points with safety stock, and economic order quantities.
2. **Which suppliers can we rely on?** Promised against actual lead times, and on-time delivery rates.
3. **What will we sell next month?** Demand forecasts, backtested against simpler methods.
4. **How much stock is enough?** A simulation of the trade-off between running out and holding stock.
5. **Does it work on real data?** The same methods applied to 1 million real transactions from a UK wholesaler.

![Reorder tab](docs/img/reorder.png)

## Drop in your messiest sales export

Real exports never match a template. Choose **My data: messy export (any format)** in the sidebar and upload whatever the accounting system, till or spreadsheet produced: Excel, CSV, or zipped.

The app does what an analyst does by hand, and logs every step:

| Problem in the file | What the app does |
|---|---|
| Title lines above the header, page breaks that repeat the header, subtotal and grand total rows | Finds the real header row; removes the extra lines |
| Unfamiliar column names (Qty, Rate, Particulars, StockCode, Vch Date ...) | Works out which column is which from names *and* contents; every guess can be corrected |
| Dates as text, day-first or month-first, Excel serial numbers | Reads them all; removes impossible dates |
| Money like ₹1,25,000.00, $1,234, (45.00), 120-, quantities like "12 pcs" | Reads them as numbers; works out unit price from line amount if needed |
| The same product spelt differently | Merges " brake pad " and "Brake Pad" |
| Duplicates, returns and credit notes, tax/freight/discount lines, free samples, huge orders keyed in by mistake and cancelled | Removes each, with a reason and examples; returns are kept for the returns view |

Then it gives answers from sales alone (trend, ABC-XYZ, a forecast tested against simpler methods, products slowing or stopped, returns, customer concentration) and a download of the cleaned data. A pre-filled sheet of the top 300 products lets the owner add stock, cost and lead time, which unlocks the full reorder dashboard.

Tested on the real 1,067,371-row UCI file as published (about 20 seconds) and on [an awful made-up accounting report](sample_data/example_messy_export.xlsx) with every common mess in it.

## Try it with your own data

Open the [live demo](https://inventory-planner-aniket.streamlit.app), choose **My own data (upload)** in the sidebar, and:

1. Download the blank Excel template (or the ready-made example, a bicycle parts distributor).
2. Fill in 3 sheets: **products**, **sales** and **stock**. **purchases** and **suppliers** are optional; with purchase order and receipt dates the tool also scores suppliers and uses actual lead times.
3. Pick whether your amounts are in US dollars or rupees, and upload it. Every tab recalculates for your business.

Money is shown in US dollars by default (the simulated company is converted at ₹83 = $1, the UK data at £1 = $1.58); a sidebar switch shows rupees instead.

The upload is checked first, with plain-English messages: missing sheets or columns, too little history (under 3 months), sales for unknown products, returns, bad dates. Common column names (SKU, Qty, Price, Date) are recognised, and Excel number IDs like 1001.0 are matched to 1001. Each upload gets its own temporary database, so visitors never see each other's data.

## Two datasets

| | Simulated manufacturer | Real wholesaler |
|---|---|---|
| What it is | A made-up Indian maker of magnetic inspection tools | UK online gift wholesaler, *Online Retail II* (UCI Machine Learning Repository) |
| Size | 24 products, 40 customers, 5 suppliers, 2 years | 1,067,371 invoice lines, 5,305 stock codes, 43 countries, 2 years |
| Why | Has stock levels, suppliers and lead times, which public data lacks | Real, messy demand, which tests the cleaning and forecasting |

All numbers for the manufacturer are simulated. None are real company figures.

## Key findings

**Simulated manufacturer**
- 9 products are at or below their reorder point. $29.7k (₹24.7 lakh) sits in idle or overstocked items.
- The instruments importer delivers on time only 36% of the time and averages 8.5 days late, so its products need extra safety stock. Using the *promised* lead time would underestimate how much.
- Raising the service level from 95% to 99% adds $6.0k of average stock ($1.5k a year to hold) for 1.2 points more demand met from stock.
- Stock turns over 3.8 times a year (96 days of inventory). Yokes are the slowest category at 133 days.
- The seasonal forecast is off by 13% over the last 6 months, against 25% for "same as last month".

**Real wholesaler**
- Cleaning removed 64,203 lines (6%) in 7 documented steps, including 34,335 duplicates and 46 huge orders keyed in by mistake and then cancelled.
- 832 of 3,789 products (22%) bring in 80% of revenue.
- ABC-XYZ: 208 products are high value with steady demand (24% of sales) and deserve the tightest planning; 1,613 are low value and erratic (3.4% of sales).
- No single forecast method wins everywhere. Seasonality cuts the error on **total** volume from 14% to 8%, but at the **product** level a plain 3-month average does best (43% against 45% for "same as last month"). Plan cash and capacity with the seasonal total; plan individual products with the simple average plus safety stock.

The full write-up is in [docs/case_study.md](docs/case_study.md).

## Methods

| Topic | Method |
|---|---|
| Safety stock | z × √(LT × σ_d² + d² × σ_LT²), covering both demand swings and late deliveries |
| Reorder point | average daily demand × actual lead time + safety stock |
| Order quantity | EOQ = √(2 × annual demand × order cost ÷ holding cost per unit) |
| Demand variability | weekly totals, because B2B orders are lumpy day to day |
| ABC | SQL window functions on cumulative revenue share (80% / 95%) |
| XYZ | coefficient of variation of monthly demand (≤0.5 steady, ≤1.0 variable, above 1.0 erratic) |
| Forecast | 3-month average, exponential smoothing and seasonal versions, each backtested month by month with no look-ahead (WAPE) |
| What-if simulation | 300 simulated years per service level, with demand and lead times resampled from history |

## How it is built

```
CSV / Excel ──► clean + validate ──► SQLite ──► SQL queries ──► Python analysis ──► Streamlit dashboard
              (src/load_db.py,                  (sql/*.sql)    (src/analysis.py,
               src/real_data.py)                               src/supply.py)
```

```
app.py                       dashboard (7 tabs, plus upload in the sidebar)
src/config.py                settings: exchange rate, service level, costs, thresholds
src/generate_sample_data.py  builds the simulated company
src/load_db.py               loads and checks the simulated data
src/analysis.py              ABC, idle stock, forecast and backtest
src/supply.py                suppliers, safety stock, EOQ, turnover, service-level simulation
src/real_data.py             downloads, cleans and analyses the real dataset
src/upload.py                Excel template, upload checks, a private database per upload
src/raw_import.py            reads and cleans any messy sales export, analyses it, builds the stock sheet
src/raw_view.py              dashboard pages for a messy export
src/make_messy_example.py    builds the awful example export
src/make_example_upload.py   builds the example file (a bicycle parts distributor)
sql/                         schema and all analysis queries
tests/                       checks for the formulas and cleaning steps
docs/                        case study and screenshots
```

## Run it

```
pip install -r requirements.txt
python src/load_db.py            # simulated company (a few seconds)
python src/real_data.py          # real dataset: downloads 45 MB, takes 2 to 3 minutes
python -m streamlit run app.py
python -m pytest -q              # 15 tests
```

![Service level what-if](docs/img/what_if.png)
![Real data tab](docs/img/real_data.png)

## Data source

Chen, D. (2019). *Online Retail II* [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D (CC BY 4.0)

## Tech

Python · pandas · NumPy · SciPy · SQL (SQLite) · Streamlit · Plotly · pytest · Git
