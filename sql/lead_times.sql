-- Every actual lead time per product (used for lead-time variability)
SELECT pu.product_id,
       JULIANDAY(pu.receipt_date) - JULIANDAY(pu.order_date) AS actual_days
FROM purchases pu;
