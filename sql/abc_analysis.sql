-- ABC analysis: which products bring in most of the money (last 12 months)
-- A = top products making ~80% of revenue, B = next 15%, C = the last 5%
WITH ref AS (
    SELECT MAX(as_of_date) AS today FROM stock
),
revenue AS (
    SELECT p.product_id,
           p.product_name,
           p.category,
           SUM(s.quantity)                     AS units_sold,
           SUM(s.quantity * s.unit_price_inr)  AS revenue_inr,
           SUM(s.quantity * (s.unit_price_inr - p.unit_cost_inr)) AS gross_profit_inr
    FROM products p
    LEFT JOIN sales s
           ON s.product_id = p.product_id
          AND s.order_date >= DATE((SELECT today FROM ref), '-12 months')
    GROUP BY p.product_id
),
ranked AS (
    SELECT *,
           SUM(COALESCE(revenue_inr, 0)) OVER (ORDER BY COALESCE(revenue_inr, 0) DESC
                                               ROWS UNBOUNDED PRECEDING) * 1.0
             / SUM(COALESCE(revenue_inr, 0)) OVER () AS cumulative_share
    FROM revenue
)
SELECT product_id, product_name, category,
       COALESCE(units_sold, 0)       AS units_sold,
       COALESCE(revenue_inr, 0)      AS revenue_inr,
       COALESCE(gross_profit_inr, 0) AS gross_profit_inr,
       ROUND(cumulative_share, 4)    AS cumulative_share,
       CASE WHEN cumulative_share - COALESCE(revenue_inr, 0) * 1.0
                 / (SELECT SUM(revenue_inr) FROM revenue) < 0.80 THEN 'A'
            WHEN cumulative_share - COALESCE(revenue_inr, 0) * 1.0
                 / (SELECT SUM(revenue_inr) FROM revenue) < 0.95 THEN 'B'
            ELSE 'C' END             AS abc_class
FROM ranked
ORDER BY revenue_inr DESC;
