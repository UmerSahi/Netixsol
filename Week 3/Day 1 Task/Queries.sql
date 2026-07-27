-- Count total records
SELECT COUNT(*) FROM superstore_sales;

-- View first 10 rows
SELECT * FROM superstore_sales
LIMIT 10;

-- View table structure
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'superstore_sales';

-- Furthur Practice Queries
-- Orders from West region
SELECT *
FROM superstore_sales
WHERE region = 'West';

-- Products with sales greater than 1000
SELECT product_name, sales
FROM superstore_sales
WHERE sales > 1000;

-- Orders with negative profit
SELECT order_id, product_name, profit
FROM superstore_sales
WHERE profit < 0;

-- Total sales by category
SELECT category, SUM(sales) AS total_sales
FROM superstore_sales
GROUP BY category;

-- Average profit by segment
SELECT segment, AVG(profit) AS average_profit
FROM superstore_sales
GROUP BY segment;

-- Top 10 profitable products
SELECT product_name, SUM(profit) AS total_profit
FROM superstore_sales
GROUP BY product_name
ORDER BY total_profit DESC
LIMIT 10;