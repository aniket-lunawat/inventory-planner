-- Units per product per month (for the forecast backtest)
SELECT STRFTIME('%Y-%m-01', invoice_date) AS month, stock_code, SUM(quantity) AS units
FROM sales
GROUP BY month, stock_code;
