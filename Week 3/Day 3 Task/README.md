# Aggregation, Subqueries, CTEs & Window Functions — Sakila DVD Rental Analysis

This project answers a series of business questions against the Sakila (DVD rental) database using SQL. It demonstrates progressively advanced querying techniques: aggregation with `GROUP BY`/`HAVING`, subqueries (scalar, correlated, `NOT EXISTS`), Common Table Expressions (CTEs), and window functions (`RANK()`, `ROW_NUMBER()`, `LAG()`).

---

## 1. Subquery vs. CTE vs. Window Function — When to Use Which

| Technique | What it does | When to reach for it |
|---|---|---|
| **Subquery** | A query nested inside another query, evaluated first (or per-row, if correlated) | Use for a **one-off, self-contained calculation** you need once — e.g., "what's the single highest value?" or "does a matching row exist?" A subquery is disposable: you don't need to name or reuse it elsewhere in the query. |
| **Correlated subquery** | A subquery that references a column from the outer query, so it re-runs once per outer row | Use when the comparison value **depends on the current row** — e.g., "the max rental rate *within this film's category*." This is naturally row-by-row logic that a plain (non-correlated) subquery can't express. |
| **CTE (`WITH ... AS`)** | A named, temporary result set defined before the main query, readable top-to-bottom | Use when a query has **multiple logical steps** that build on each other (aggregate → rank → filter), or when the **same intermediate result is referenced more than once**. CTEs turn a deeply nested query into readable, sequential stages — much easier to debug and maintain than nested subqueries. |
| **Window function** (`OVER (PARTITION BY ... ORDER BY ...)`) | Performs a calculation across a set of rows related to the current row, **without collapsing them** into a single output row (unlike `GROUP BY`) | Use when you need a **rank, running total, previous-row value, or per-group comparison** while still returning every individual row — e.g., "rank each customer within their city" or "show this month's revenue next to last month's." `GROUP BY` can't do this because it discards row-level detail. |

**Rule of thumb used throughout this project:**
- If the logic is a single lookup value → **subquery**.
- If the value needed depends on the outer row → **correlated subquery**.
- If the query has multiple sequential steps or the same result is reused → **CTE**.
- If rows need to be ranked, ordered, or compared to previous rows *without losing row-level detail* → **window function** (almost always combined with a CTE for readability).

---

## 2. How Each Business Question Was Solved

**Total revenue per store**
Joined `payment → staff → store` and aggregated with `SUM(amount)` grouped by `store_id`, since revenue only exists at the payment level and has to be traced back to the store through the staff member who processed it.

**Average rental duration per film category**
Two approaches were used:
1. `AVG(f.rental_duration)` — the film table's *listed* rental period (in days), joined through `film_category → category`.
2. `AVG(return_date - rental_date)` — the *actual* time customers took to return films, joined through `rental → inventory → film`. This is the more business-realistic version, filtered to `return_date IS NOT NULL` to exclude rentals still outstanding.

**Number of rentals made each month**
Grouped rentals by month using `DATE_TRUNC('month', rental_date)` for correct chronological ordering, with `TO_CHAR` used purely for a human-readable label in the output.

**Categories with more than 50 films**
Aggregated film counts per category, then filtered on the aggregate itself using `HAVING COUNT(...) > 50` — `HAVING` is required here (not `WHERE`) because the filter applies to a computed aggregate, which doesn't exist until after grouping.

**Customers who spent more than the average customer spend**
A subquery first computes each customer's total spend, wraps that in `AVG(...)` to get the average *customer* spend (not the average *payment*), and the outer query keeps only customers exceeding that benchmark via `HAVING`.

**Film(s) with the highest rental rate in each category**
A **correlated subquery** compares each film's rate to the max rate *within its own category* (`fc2.category_id = fc.category_id`), so the comparison threshold changes per row — this could not be done with a single flat subquery.

**Customers who have never rented a film**
`NOT EXISTS` checks, per customer, whether any matching row exists in `rental`. This is generally preferred over `NOT IN` because it handles `NULL`s safely and is typically more efficient on large tables.

**Store with the highest total revenue (subquery in WHERE)**
An inner query aggregates revenue per store, sorts descending, and takes the top row via `LIMIT 1`; the outer query then filters the full result set down to that one store's rows.

**Rank customers by total spend within each city (CTE)**
A CTE first computes each customer's total spend joined through `address → city`. The outer query then applies `RANK() OVER (PARTITION BY city_name ORDER BY total_spent DESC)` so customers are ranked *within* their own city rather than across the whole customer base.

**Most recently rented film per customer (`ROW_NUMBER()`)**
A CTE ranks each customer's rentals by date descending using `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY rental_date DESC)`, and the outer query keeps only `rn = 1` — the single most recent rental per customer.

**Month-over-month rental revenue growth (CTE)**
The first CTE aggregates revenue by month; the second uses `LAG(total_revenue) OVER (ORDER BY revenue_month)` to pull the *previous* month's revenue onto the same row, enabling a direct percentage-growth calculation.

**Top 3 highest-grossing films per category (`RANK()` in a CTE)**
Revenue per film per category is aggregated in one CTE, then ranked within each category using `RANK() OVER (PARTITION BY category_name ORDER BY total_revenue DESC)` in a second CTE. The outer query filters to `revenue_rank <= 3`.

**Top-revenue staff member per store + their % contribution**
Three CTEs chained together: (1) revenue per staff member, (2) total revenue per store, (3) staff ranked within their store. The final query joins the ranked staff (keeping only rank 1) against store totals to compute each top performer's percentage contribution — a good example of combining aggregation, ranking, and arithmetic in one pipeline.

---

## 3. Three Business Insights

1. **Each store currently runs on a single staff member handling 100% of its revenue — a real single-point-of-failure risk.** The staff-revenue query shows Store 1's entire $30,252.12 in revenue processed by Mike Hillyer, and Store 2's entire $31,059.92 processed by Jon Stephens — both at exactly 100% of their store's total. Store 2 is modestly ahead of Store 1 (~2.7% more revenue), but the bigger finding is structural: neither store has a second active staff member recorded as processing any payments. Operationally, if either employee were out, there is currently no backup on record capturing that store's transactions — worth flagging even though this reflects the dataset as-is rather than a trend to fix.

2. **Revenue growth is decelerating sharply month over month.** The MoM growth query shows revenue jumping from $8,351.84 (Feb 2007) to $23,886.56 (Mar 2007) — a **+186% spike** — then slowing to just **+19.56%** growth into April 2007 ($28,559.46). That pattern (a large initial ramp followed by a much smaller increase) is typical of an early-adoption or onboarding period stabilizing into steadier, more modest growth. Worth watching in the next month or two of data to see whether growth keeps decelerating toward flat, or holds around this ~20% level.

3. **There are zero dormant customers — the entire registered customer base has rented at least once.** The "customers who never rented" query returned 0 of 0 rows, meaning every customer on record has real rental history. That's a genuinely good sign (no wasted/never-converted sign-ups), but it also means **retention strategy can't target "inactive accounts"** because there aren't any — it needs to shift to **recency and frequency** instead (e.g., customers who haven't rented *recently*, not customers who've never rented at all). Combined with the top-spender query — where the highest customer spend tops out around $183–$212, well above the customer-wide average that triggered the `HAVING` filter — there's a clear opportunity to build a loyalty tier around that top group specifically, since they're both proven repeat renters and well above-average spenders.

**One more pattern worth a follow-up look:** the "highest rental rate per category" query shows every top film across every category priced at exactly **$4.99**, the apparent ceiling rate in this dataset. Categories aren't differentiating their top-tier pricing from each other — an opportunity to test premium pricing on the highest-demand categories (per the top-3-films-by-revenue query, Comedy and Documentary post the highest per-film revenue: Zorro Ark at $199.72 and Wife Turn at $198.73) rather than capping every category at the same top rate.

---

## Files

- `aggregation_subqueries.sql` —  This file has all queries referenced in this README, in execution order.
