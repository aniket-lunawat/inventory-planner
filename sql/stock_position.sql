-- Current stock with the product details needed for reorder maths
SELECT p.product_id, p.product_name, p.category,
       p.unit_cost_inr, p.lead_time_days,
       sup.supplier_name,
       st.on_hand_units, st.as_of_date
FROM products p
JOIN stock st          ON st.product_id = p.product_id
LEFT JOIN suppliers sup ON sup.supplier_id = p.supplier_id;
