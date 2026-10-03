-- One row per customer: how much they bought in the last 12 months, how often, and when last.
-- Customers recorded as UNKNOWN (no ID on the invoice) are left out.
WITH ref AS (SELECT MAX(as_of_date) AS today FROM stock),
lines AS (
    SELECT s.customer_id, s.invoice_no, s.order_date, s.quantity,
           s.quantity * s.unit_price_inr AS revenue
    FROM sales s
    WHERE s.customer_id IS NOT NULL AND s.customer_id <> 'UNKNOWN'
)
SELECT l.customer_id,
       COALESCE(c.customer_name, l.customer_id)                                         AS customer_name,
       COALESCE(c.segment, '')                                                          AS segment,
       SUM(CASE WHEN l.order_date >= DATE(r.today, '-12 months') THEN l.revenue END)    AS revenue_12m,
       COUNT(DISTINCT CASE WHEN l.order_date >= DATE(r.today, '-12 months')
                           THEN l.invoice_no || l.order_date END)                       AS orders_12m,
       SUM(CASE WHEN l.order_date >= DATE(r.today, '-3 months') THEN l.revenue ELSE 0 END)  AS revenue_last_3m,
       SUM(CASE WHEN l.order_date >= DATE(r.today, '-6 months')
                 AND l.order_date <  DATE(r.today, '-3 months') THEN l.revenue ELSE 0 END)  AS revenue_prior_3m,
       MIN(l.order_date)                                                                AS first_order,
       MAX(l.order_date)                                                                AS last_order,
       COUNT(DISTINCT l.invoice_no || l.order_date)                                     AS orders_all_time
FROM lines l
CROSS JOIN ref r
LEFT JOIN customers c ON c.customer_id = l.customer_id
GROUP BY l.customer_id
ORDER BY revenue_12m DESC;
