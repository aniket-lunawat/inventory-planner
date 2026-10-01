-- Units sold and cost of goods sold per product over the last 12 months
SELECT p.product_id,
       p.product_name,
       p.unit_cost_inr,
       COALESCE(SUM(s.quantity), 0)                      AS annual_units,
       COALESCE(SUM(s.quantity), 0) * p.unit_cost_inr    AS annual_cogs_inr
FROM products p
LEFT JOIN sales s
       ON s.product_id = p.product_id
      AND s.order_date >= DATE((SELECT MAX(as_of_date) FROM stock), '-12 months')
GROUP BY p.product_id;
