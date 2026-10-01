-- Revenue and units per month, per product category
SELECT STRFTIME('%Y-%m', s.order_date)       AS month,
       p.category,
       SUM(s.quantity)                       AS units,
       SUM(s.quantity * s.unit_price_inr)    AS revenue_inr
FROM sales s
JOIN products p ON p.product_id = s.product_id
GROUP BY month, p.category
ORDER BY month, p.category;
