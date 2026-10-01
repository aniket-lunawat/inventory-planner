-- ABC analysis on the real wholesaler data (last 12 full months: Dec 2010 - Nov 2011)
WITH revenue AS (
    SELECT stock_code,
           MAX(description)         AS description,
           SUM(quantity)            AS units,
           SUM(revenue_gbp)         AS revenue_gbp,
           COUNT(DISTINCT invoice)  AS orders
    FROM sales
    WHERE invoice_date >= '2010-12-01' AND invoice_date < '2011-12-01'
    GROUP BY stock_code
),
ranked AS (
    SELECT *,
           SUM(revenue_gbp) OVER (ORDER BY revenue_gbp DESC ROWS UNBOUNDED PRECEDING)
             / SUM(revenue_gbp) OVER ()                          AS cumulative_share,
           revenue_gbp / SUM(revenue_gbp) OVER ()                AS share
    FROM revenue
)
SELECT stock_code, description, units, orders,
       ROUND(revenue_gbp, 2)                  AS revenue_gbp,
       ROUND(cumulative_share, 4)             AS cumulative_share,
       CASE WHEN cumulative_share - share < 0.80 THEN 'A'
            WHEN cumulative_share - share < 0.95 THEN 'B'
            ELSE 'C' END                      AS abc_class
FROM ranked
ORDER BY revenue_gbp DESC;
