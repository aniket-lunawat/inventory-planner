-- Return rate by country: value cancelled / value sold (countries with £20k+ sales)
WITH sold AS (
    SELECT country, SUM(revenue_gbp) AS sold_gbp FROM sales GROUP BY country
),
returned AS (
    SELECT country, SUM(value_gbp) AS returned_gbp FROM returns GROUP BY country
)
SELECT s.country,
       ROUND(s.sold_gbp, 0)                                       AS sold_gbp,
       ROUND(COALESCE(r.returned_gbp, 0), 0)                      AS returned_gbp,
       ROUND(100.0 * COALESCE(r.returned_gbp, 0) / s.sold_gbp, 1) AS return_rate_pct
FROM sold s
LEFT JOIN returned r ON r.country = s.country
WHERE s.sold_gbp >= 20000
ORDER BY return_rate_pct DESC;
