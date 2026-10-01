-- Idle stock: products still on the shelf that haven't sold in a long time
-- :idle_days is filled in by Python (180 by default)
SELECT p.product_id,
       p.product_name,
       st.on_hand_units,
       st.on_hand_units * p.unit_cost_inr                     AS stock_value_inr,
       MAX(s.order_date)                                     AS last_sale_date,
       CAST(JULIANDAY(st.as_of_date) - JULIANDAY(MAX(s.order_date)) AS INTEGER)
                                                             AS days_since_last_sale
FROM products p
JOIN stock st      ON st.product_id = p.product_id
LEFT JOIN sales s  ON s.product_id = p.product_id
GROUP BY p.product_id
HAVING st.on_hand_units > 0
   AND (last_sale_date IS NULL OR days_since_last_sale >= :idle_days)
ORDER BY stock_value_inr DESC;
