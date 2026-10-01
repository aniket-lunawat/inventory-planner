# Case study: inventory planning for a small manufacturer

**Aniket Lunawat** · Python, SQL, Streamlit · October 2026

## The problem

Small manufacturers often decide what to make and when to reorder from experience. That leads to two expensive mistakes at once: running out of the products customers want, and tying up cash in products nobody is buying. The goal was a tool a non-technical owner can check once a week and act on in a few minutes.

Real company data was not available, so I built a simulated company modeled on a small Indian maker of magnetic inspection tools (24 products, 40 customers, 5 suppliers, 2 years of invoices) with realistic patterns: a March financial year end rush, a Diwali slowdown, growing and declining products, dead items and unreliable suppliers. To check that the methods hold up on real data, I applied them to a public dataset of 1 million transactions from a UK wholesaler.

## What I did

1. **Database.** Designed a 6-table SQLite schema (products, suppliers, customers, sales, stock, purchases) with checks for bad rows, and wrote the analysis queries in SQL, including window functions for ABC analysis.
2. **Reorder logic.** Safety stock that accounts for both demand variation and supplier delays, reorder points based on *actual* lead times, and economic order quantities.
3. **Supplier scorecard.** Promised against actual lead time and on-time rate per supplier.
4. **Forecast.** Seasonal forecast, backtested month by month without look-ahead against simple baselines.
5. **What-if simulation.** For service levels from 90% to 99%, simulated 300 years of daily operations each, resampling real demand and real lead times, to measure fill rate against stock held.
6. **Real data.** Cleaned 1,067,371 invoice lines in 7 documented steps, then ran ABC-XYZ, a 6-method forecast comparison and a returns analysis.

## Findings (simulated manufacturer)

| Finding | Number |
|---|---|
| Products to reorder now | 9 of 24, order worth ₹7.5 lakh |
| Money in idle or overstocked items | ₹24.7 lakh (≈$29.7k) |
| Least reliable supplier | on time 36% of deliveries, 8.5 days late on average |
| Inventory turnover | 3.8 turns a year (96 days); yokes slowest at 133 days |
| Cost of raising service level from 95% to 99% | +₹5.0 lakh average stock (+₹1.2 lakh a year to hold) for +1.2 points of demand met |
| Forecast error, last 6 months | 13%, against 25% for "same as last month" |

## Findings (real UK wholesaler)

| Finding | Number |
|---|---|
| Lines removed in cleaning | 64,203 (6%): duplicates, cancellations, non-product codes, write-offs, mistaken bulk orders |
| Products making 80% of revenue | 832 of 3,789 (22%) |
| High value, steady demand (AX) | 208 products, 24% of sales |
| Low value, erratic demand (CZ) | 1,613 products, 3.4% of sales |
| Best forecast for total volume | exponential smoothing with seasonality, 8% error (14% for "same as last month") |
| Best forecast per product | 3-month average, 43% error (45% for "same as last month") |
| Highest return rate | Norway, 54% of value sold |

## Recommendations

1. **Order the 9 flagged products now**, starting with the aerosols, which have under a week of stock left.
2. **Clear idle stock** (₹3.6 lakh with no sale in 6 months) through discounts to regular customers or bundles, and pause production of the overstocked AC yoke (6 months of stock).
3. **Hold extra safety stock for the importer's products**, or find a second source. Its delays, not demand, drive most of the stockout risk on those items.
4. **Target 95% service** for most products. Going to 99% costs about ₹1.2 lakh a year in holding cost for a small gain; reserve it for the products that matter most (the AX group).
5. **Forecast at two levels.** Use the seasonal model for total volume (cash, staffing, capacity) and a simple average plus safety stock for individual products. Single-product demand is too noisy for seasonality to help.

## Limitations

- The manufacturer's data is simulated, so its findings show the method, not a real company's results.
- Stock history is not available, so turnover uses today's stock as the average.
- The simulation treats unmet demand as lost; in practice some customers would wait.
- The real dataset has no stock or supplier data, so only demand-side methods were tested on it.
