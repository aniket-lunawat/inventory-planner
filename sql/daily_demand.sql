-- Units sold per product per day (days with no sales are filled in by Python)
SELECT product_id, order_date, SUM(quantity) AS units
FROM sales
GROUP BY product_id, order_date;
