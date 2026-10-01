-- Lesson 1: SELECT, FROM, WHERE, ORDER BY
-- Products that sell for more than ₹5,000, most expensive first.
SELECT product_name, unit_price_inr
FROM products
WHERE unit_price_inr > 5000
ORDER BY unit_price_inr DESC;
