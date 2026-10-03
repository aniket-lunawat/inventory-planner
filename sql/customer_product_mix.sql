-- Units each customer bought of each product in the last 12 months, and that customer's
-- share of the product's total demand (a window function over each product).
WITH ref AS (SELECT MAX(as_of_date) AS today FROM stock),
mix AS (
    SELECT s.customer_id, s.product_id,
           SUM(s.quantity)                    AS units,
           SUM(s.quantity * s.unit_price_inr) AS revenue
    FROM sales s
    CROSS JOIN ref r
    WHERE s.order_date >= DATE(r.today, '-12 months')
      AND s.customer_id IS NOT NULL AND s.customer_id <> 'UNKNOWN'
    GROUP BY s.customer_id, s.product_id
)
SELECT m.customer_id, m.product_id, p.product_name, m.units, m.revenue,
       m.units * 1.0 / SUM(m.units) OVER (PARTITION BY m.product_id) AS share_of_product
FROM mix m
JOIN products p ON p.product_id = m.product_id;
