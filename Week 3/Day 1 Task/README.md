# PostgreSQL Superstore Dataset Import

## Project Overview

This project demonstrates how to import a CSV dataset into PostgreSQL using pgAdmin and perform basic SQL queries for data exploration.

The dataset used is the **Superstore Dataset**, which contains sales transactions, customer information, product details, shipping information, and profit data.

---

## Dataset

**Source:**
https://www.kaggle.com/datasets/vivek468/superstore-dataset-final

Dataset Name:
**Sample - Superstore.csv**

---

## Tools Used

- PostgreSQL 17
- pgAdmin 4
- SQL

---

## Project Steps

1. Downloaded the Superstore dataset from Kaggle.
2. Created a new PostgreSQL database named `superstore_db`.
3. Created a table named `superstore_sales`.
4. Imported the CSV file into PostgreSQL using pgAdmin.
5. Verified the imported data using SQL queries.
6. Executed filtering, sorting, and aggregation queries.

---

## Database Structure

Database Name:

```
superstore_db
```

Table Name:

```
superstore_sales
```

---

## SQL Operations Performed

- CREATE TABLE
- SELECT
- WHERE
- ORDER BY
- LIMIT
- COUNT()
- SUM()
- AVG()
- GROUP BY
- Aggregate Functions

---

## Example Queries

### Count Total Records

```sql
SELECT COUNT(*)
FROM superstore_sales;
```

### View First 10 Records

```sql
SELECT *
FROM superstore_sales
LIMIT 10;
```

### Display Table Structure

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'superstore_sales';
```

### Orders from West Region

```sql
SELECT *
FROM superstore_sales
WHERE region = 'West';
```

### Products with Sales Greater Than 1000

```sql
SELECT product_name, sales
FROM superstore_sales
WHERE sales > 1000;
```

### Orders with Negative Profit

```sql
SELECT order_id, product_name, profit
FROM superstore_sales
WHERE profit < 0;
```

### Total Sales by Category

```sql
SELECT category,
       SUM(sales) AS total_sales
FROM superstore_sales
GROUP BY category;
```

### Average Profit by Segment

```sql
SELECT segment,
       AVG(profit) AS average_profit
FROM superstore_sales
GROUP BY segment;
```

### Top 10 Most Profitable Products

```sql
SELECT product_name,
       SUM(profit) AS total_profit
FROM superstore_sales
GROUP BY product_name
ORDER BY total_profit DESC
LIMIT 10;
```

---

## Repository Contents

```
README.md
concept_check.md
queries.sql
screenshots/
```

---

## Learning Outcomes

Through this project, I learned how to:

- Create databases in PostgreSQL
- Create tables using SQL
- Import CSV files into PostgreSQL
- Execute SQL queries
- Filter data using WHERE
- Sort data using ORDER BY
- Limit query results using LIMIT
- Perform aggregation using COUNT(), SUM(), and AVG()
- Group data using GROUP BY
- Explore database metadata using `information_schema.columns`

---

## Author

**Muhammad Umer Sahi**