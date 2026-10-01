-- Supplier scorecard: promised vs actual lead time, and how often they deliver on time
-- :grace is filled in by Python (days late that still count as on time)
WITH deliveries AS (
    SELECT pu.supplier_id,
           p.lead_time_days                                            AS promised_days,
           JULIANDAY(pu.receipt_date) - JULIANDAY(pu.order_date)       AS actual_days
    FROM purchases pu
    JOIN products p ON p.product_id = pu.product_id
)
SELECT s.supplier_id,
       s.supplier_name,
       COUNT(*)                                                         AS deliveries,
       ROUND(AVG(promised_days), 1)                                     AS avg_promised_days,
       ROUND(AVG(actual_days), 1)                                       AS avg_actual_days,
       ROUND(AVG(actual_days - promised_days), 1)                       AS avg_days_late,
       ROUND(100.0 * SUM(actual_days <= promised_days + :grace) / COUNT(*), 0)
                                                                        AS on_time_pct
FROM deliveries d
JOIN suppliers s ON s.supplier_id = d.supplier_id
GROUP BY s.supplier_id
ORDER BY on_time_pct;
