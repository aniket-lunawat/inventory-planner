-- How late each supplier is, as actual lead time divided by promised lead time.
-- A ratio of 1.0 is on time; 1.25 means deliveries take 25% longer than promised.
-- Used to estimate lead times for a product the supplier hasn't delivered yet.
SELECT pu.supplier_id,
       s.supplier_name,
       (JULIANDAY(pu.receipt_date) - JULIANDAY(pu.order_date)) * 1.0 / p.lead_time_days AS ratio
FROM purchases pu
JOIN products p  ON p.product_id = pu.product_id
JOIN suppliers s ON s.supplier_id = pu.supplier_id
WHERE p.lead_time_days > 0;
