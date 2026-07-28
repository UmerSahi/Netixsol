# Concept Check — Relational Databases & SQL Joins

### 1. Why do relational databases split data into multiple tables?

Splitting data into multiple tables avoids storing the same information
repeatedly. For example, in the dvdrental database, a customer's city
isn't copied into every rental or payment row, it's stored once in
`address`/`city`, and other tables just reference it by ID. This keeps
data consistent (update a city name once, not thousands of times),
saves storage, and reduces the risk of contradictory data (e.g. one row
saying "London" and another saying "Londonn" for the same customer).
This practice is called **normalization**.

---

### 2. Difference between INNER JOIN and LEFT JOIN

- **INNER JOIN** returns only the rows that have a match in *both* tables.
  If a row in the left table has no matching row in the right table, it's
  left out entirely.
- **LEFT JOIN** (LEFT OUTER JOIN) returns *all* rows from the left table,
  whether or not they have a match in the right table. If there's no
  match, the right table's columns just show as `NULL`.

**Example:** `customer LEFT JOIN payment` would return every customer,
even ones who've never made a payment (their payment columns would be
`NULL`). The same query with `INNER JOIN` would drop those customers
from the results entirely.

---

### 3. When would you use a FULL OUTER JOIN?

Use a `FULL OUTER JOIN` when you want *every* row from both tables,
matched where possible, with `NULL`s filling in the gaps on either side.
It's useful when you need to spot mismatches in both directions at once —
for example, finding both customers with no payments **and** payments
with no valid customer record, in a single query. It's less common in
day-to-day reporting than `INNER`/`LEFT` joins, but valuable for data
auditing and reconciliation tasks.

---

### 4. Why are Primary Keys and Foreign Keys important?

- A **Primary Key (PK)** uniquely identifies each row in a table (e.g.
  `customer_id` in `customer`). No two rows can share the same PK value,
  and it can't be `NULL`.
- A **Foreign Key (FK)** is a column in one table that references a
  Primary Key in another table (e.g. `customer.address_id` references
  `address.address_id`). This is what makes JOINs possible — it's the
  "link" between tables.

Together, they enforce **referential integrity**: the database won't let
you insert a rental for a `customer_id` that doesn't exist, which
prevents orphaned or inconsistent data.

---

### 5. Explain normalization in simple words

Normalization means organizing a database so that each piece of
information is stored **in exactly one place**, and tables only contain
data that directly relates to what that table represents. Instead of one
giant table with repeated, tangled data, you break it into smaller,
focused tables connected by keys like splitting `customer`, `address`,
`city`, and `country` into separate tables instead of cramming full
address text into every customer row. The goal is to reduce redundancy
and make the data easier to update correctly.

---

### 6. What is an ER Diagram?

An **Entity-Relationship (ER) Diagram** is a visual map of a database's
structure. It shows each table ("entity") as a box listing its columns,
and draws lines between tables to show how they're related (one-to-many,
many-to-many, etc.) via their primary/foreign keys. It's the blueprint
that shows how tables like `customer`, `rental`, and `payment` connect —
exactly like the ERD you generated for the dvdrental database.

---

### 7. What happens if a JOIN condition is incorrect?

If the `ON` condition is wrong or missing:

- **Wrong condition** (e.g. joining on the wrong columns) usually
  produces incorrect or misleading results — rows get matched to the
  wrong counterpart, silently corrupting your output without throwing
  an error.
- **Missing condition** (e.g. `FROM a, b` with no `ON`/`WHERE` clause, or
  a `CROSS JOIN`) produces a **Cartesian product** every row in table A
  gets paired with every row in table B. On small tables this might just
  look like "too many rows"; on large tables (like `payment` with
  thousands of rows) it can produce millions of nonsensical rows and
  seriously slow down or crash the query.

Either way, the query usually still *runs* the danger is that it
doesn't error out, it just quietly returns wrong data, which is why
double-checking JOIN conditions and row counts matters.
