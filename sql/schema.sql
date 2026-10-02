-- Inventory database (simulated manufacturer)
-- Five tables. Each sale, stock count and purchase points back to a product.

DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS purchases;
DROP TABLE IF EXISTS stock;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS suppliers;

CREATE TABLE suppliers (
    supplier_id    TEXT PRIMARY KEY,
    supplier_name  TEXT NOT NULL,
    location       TEXT
);

CREATE TABLE products (
    product_id      TEXT PRIMARY KEY,
    product_name    TEXT NOT NULL,
    category        TEXT,
    unit_cost_inr   REAL NOT NULL,   -- what it costs the company to make or buy
    unit_price_inr  REAL NOT NULL,   -- list selling price
    supplier_id     TEXT REFERENCES suppliers(supplier_id),
    lead_time_days  INTEGER NOT NULL -- days from placing an order to having stock
);

CREATE TABLE customers (
    customer_id    TEXT PRIMARY KEY,
    customer_name  TEXT NOT NULL,
    segment        TEXT
);

-- One row per invoice line (an invoice can have several lines)
CREATE TABLE sales (
    line_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT,
    order_date      DATE NOT NULL,
    customer_id     TEXT REFERENCES customers(customer_id),
    product_id      TEXT NOT NULL REFERENCES products(product_id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_inr  REAL NOT NULL
);

-- Latest stock count per product
CREATE TABLE stock (
    product_id     TEXT PRIMARY KEY REFERENCES products(product_id),
    on_hand_units  INTEGER NOT NULL,
    as_of_date     DATE NOT NULL
);

-- Goods received into stock (bought in or finished in-house)
CREATE TABLE purchases (
    receipt_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    order_date     DATE NOT NULL,    -- when the purchase order was placed
    receipt_date   DATE NOT NULL,    -- when the goods arrived
    product_id     TEXT NOT NULL REFERENCES products(product_id),
    supplier_id    TEXT REFERENCES suppliers(supplier_id),
    quantity       INTEGER NOT NULL,
    unit_cost_inr  REAL NOT NULL
);

CREATE INDEX idx_sales_date    ON sales(order_date);
CREATE INDEX idx_sales_product ON sales(product_id);
