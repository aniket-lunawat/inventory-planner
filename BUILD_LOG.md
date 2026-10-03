# Build Log

Short notes after every session: what I did, what I decided, and why.

## 2026-09-23 - setup and first version
- Set up laptop: Python 3.13, VS Code, Git (on D drive).
- Real company data was not available, so I built a simulated company.
- Built the first version on sample data while waiting for real data:
  - SQLite database with 6 tables (suppliers, products, customers, sales, stock, purchases).
  - Loader with cleaning and data checks (unknown products, missing stock rows, price below cost).
  - ABC analysis in SQL with window functions.
  - Reorder points with safety stock.
    - Decision: use weekly, not daily, demand variation. With daily variation, 14 of 24 products
      said "order now" because one big B2B order on one day blows up the standard deviation.
    - Decision: items selling under 1 unit a month are "make to order". The dashboard was
      telling us to stock 3 more ₹2.85 lakh benches.
  - Forecast: 3-month average, adjusted for seasonality. Backtest: 13% error vs 25% for
    "same as last month".
  - Streamlit dashboard with 4 tabs; money in ₹ and USD.

## 2026-09-30 - decided to stay on simulated data
- Real company data isn't available, so the project runs on the simulated company.
- Relabelled the dashboard and README so every number is clearly simulated.

## 2026-10-01 - made it a proper project
- Suppliers now have order dates, so actual lead times can be measured. Built a supplier scorecard in SQL.
  - The importer is on time only 36% of the time. Safety stock now uses actual lead time and its
    variation: SS = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2).
- Added EOQ (order cost Rs 1,500, holding 25% a year) and inventory turnover.
- Built a service-level simulation (300 simulated years per level).
  - 95% to 99% costs Rs 5 lakh more stock for 1.2 points more demand met.
- Added the real UCI Online Retail II dataset (1,067,371 lines).
  - 7 cleaning steps, each logged. Found 46 bulk orders keyed in by mistake and cancelled.
  - Decision: keep lines with missing customer IDs for demand; they are real sales.
  - ABC-XYZ on monthly demand (weekly made almost everything "erratic").
  - Forecast surprise: my seasonal method lost to "same as last month" per product. Tested 6 methods.
    Seasonality helps for total volume (8% error) but not per product (3-month average best, 43%).
- Fixed a display bug: two "$" signs in one line rendered as a maths formula in Streamlit.
- 7 pytest tests. README with screenshots, one-page case study in docs/.

## 2026-10-02 - anyone can upload their own data
- Sidebar option "My own data (upload)": blank Excel template, an example file, and an uploader.
- Only products, sales and stock are required. Without purchases the tool falls back to promised
  lead times and skips the supplier scorecard instead of crashing.
- Upload checks (src/upload.py): errors stop the upload (missing sheet or column, under 3 months of
  sales); warnings skip bad rows and say how many (returns, unknown products, bad dates).
  - Decision: accept common header names (SKU, Qty, Price) and turn Excel's 1001.0 back into 1001,
    because real exports never match a template exactly.
  - Decision: sales invoice_no is no longer the primary key. Real invoices have several lines.
- Each upload gets its own temporary SQLite file, and the active database is stored per visitor
  (thread), so two people uploading at once never see each other's data.
- Short histories: the backtest tests fewer months, and seasonality is only used with 12+ months.
- 3 new tests (10 total). Tested in a browser with a second made-up company (bike parts).

## 2026-10-02 - money in US dollars
- The dashboard shows US dollars by default, with a sidebar switch to rupees.
  - The simulated company is still stored in rupees and converted at one rate (config.INR_PER_USD).
  - The UK case study is converted from pounds at about the 2010-2011 average (config.USD_PER_GBP).
  - Uploads: the visitor says whether their file is in dollars or rupees (default dollars).
- Decision: the order cost (Rs 1,500, about $18) is converted into the data's currency, so EOQ comes
  out the same whichever currency the data is in. A test checks this.
- Template columns are now unit_cost and unit_price (no "_inr"); old names still work.
- Example file and test companies saved in dollars. 12 tests.

## 2026-10-02 - messy exports: the tool should work on any company's worst data
- Tried the real UCI file exactly as published: the upload rejected it (no products/stock sheets, own column names,
  1 million rows). Real exports never match a template, so I built a third data mode.
- src/raw_import.py: reads any Excel/CSV/gzip/zip, finds the header row under title lines, guesses each column from
  its name and contents (the user can correct any guess), parses any number/date format, and cleans in logged steps.
  - Decision: never drop rows silently. Every step records rows removed, value removed, why, and examples.
  - Decision: tax/freight/discount lines are removed BEFORE returns, because a discount line is negative but not a return.
  - Decision: cancellations of mistaken bulk orders are removed from returns too, or they'd look like huge returns.
  - Bugs found by tests: 'Rs.450' read as 0.45; ISO dates read day-first; 'dd-mm-yyyy' text taken for numbers.
- Sales alone give: trend, ABC-XYZ, a backtested forecast, slowing/stopped products, returns, customers, clean CSV.
- A pre-filled stock sheet (top 300 products) unlocks the full reorder dashboard.
- Real file: 1,067,371 rows -> 1,003,039 clean lines; 830 of 3,759 products make 80% of sales (my hand-built
  pipeline said 832). About 20 seconds; memory kept under 1.3 GB by storing repeated text as categories and
  reading Excel one sheet at a time (the free host allows up to 2.7 GB).
- 15 tests.

## 2026-10-03 - planner adjustments and supplier comparison
- Demand adjustments: product (or all) x % x months x reason. They scale next month's forecast, and the demand
  used for reorder planning when they fall inside a product's reorder window (today to lead time + 1 month).
  - Decision: an "Apply" button instead of live updates, so half-typed rows don't change the plan.
  - Decision: the backtest stays unadjusted, so forecast accuracy still measures the model, not my guesses.
- Supplier comparison on total yearly cost = purchases + ordering + cycle stock + safety stock holding.
  - New SQL: supplier_lead_ratio.sql (actual / promised lead time per delivery), so a supplier's track
    record can be applied to a product it hasn't supplied yet. No history -> assume an average supplier.
  - Finding: price usually dominates. Reliability wins only when the price gap is small or demand is high.
- 3 new tests (18). Bug caught in the browser: a variable named `price` hid the new price() function.

## Next
- Go through every SQL query and be able to explain it without notes.
- Put it on GitHub, then online (Streamlit Community Cloud).
