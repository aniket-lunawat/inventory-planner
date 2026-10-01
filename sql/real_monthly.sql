-- Revenue, orders and active customers per month
SELECT STRFTIME('%Y-%m-01', invoice_date)  AS month,
       SUM(revenue_gbp)                    AS revenue_gbp,
       COUNT(DISTINCT invoice)             AS orders,
       COUNT(DISTINCT customer_id)         AS customers
FROM sales
GROUP BY month
ORDER BY month;
