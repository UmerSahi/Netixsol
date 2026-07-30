# SQL Concept Check

---

## 1. Why are multiple CTEs preferred over one large nested query?

A single deeply nested query (subqueries inside subqueries inside joins) forces a reader to parse from the innermost query outward just to understand what the final `SELECT` is even working with. Multiple CTEs (`WITH a AS (...), b AS (...), c AS (...)`) instead let each stage be named, read top-to-bottom in the order the logic actually happens, and reasoned about independently.

Concretely, in the pipeline built in this conversation:
- **Readability** — `customer_purchases` → `customer_profile` → `customer_segments` reads like a sentence: join the data, aggregate it, classify it. A nested version of the same logic would bury the join three or four levels deep inside parentheses.
- **Debuggability** — if a number looks wrong, you can run `SELECT * FROM customer_profile LIMIT 20` on its own to inspect that one stage, without extracting a fragment from the middle of a nested query.
- **Reuse within the same query** — a CTE can be referenced multiple times later in the query (e.g., `customer_purchases` was reused by both `customer_profile` and `customer_genre_counts`) without duplicating the join logic or wrapping it in a view.
- **Easier collaboration** — someone else picking up the query later can see the pipeline's stages as a table of contents, rather than reverse-engineering intent from nesting depth.

The tradeoff is that CTEs aren't automatically more performant — Postgres may materialize or re-run a CTE depending on the version and query planner — but the clarity and maintainability gain is almost always worth it for anything beyond a trivial two-table join.

---

## 2. When would you use a window function instead of GROUP BY?

`GROUP BY` **collapses** rows — you lose the individual row and are left only with one row per group. A window function **keeps every row** while still letting you compute an aggregate-like value across a group, because it computes the calculation "over a window" of related rows without collapsing them.

Use a window function instead of `GROUP BY` when you need:

- **A ranking within groups without losing row-level detail** — e.g. `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total_tracks DESC)` in `favorite_genres`. `GROUP BY` alone can't produce a per-row rank; it can only tell you the max or the count.
- **A group-level value used side-by-side with row-level values** — e.g. `MAX(total_revenue) OVER()` in `country_scores`, where each country's own row still needs to be visible next to the global maximum, in order to divide the two.
- **Running totals, moving averages, or "compared to previous row" logic** — things like `SUM(...) OVER (ORDER BY invoice_date)` for a running total, which `GROUP BY` fundamentally can't express since it has no concept of row order within a group.
- **Percent-of-total calculations** — e.g. `country_revenue.revenue_percentage`, where each row needs both its own value and the grand total in the same row.

Rule of thumb: if the output should have **the same number of rows as the input** (just enriched with a group-aware value), use a window function. If the output should have **fewer rows** (one per group), use `GROUP BY`.

---

## 3. Explain the difference between ROW_NUMBER(), RANK(), and DENSE_RANK().

All three assign an ordinal position to rows within a window (typically `PARTITION BY ... ORDER BY ...`), but they handle **ties** differently:

| Function | Behavior on ties | Example output for values (100, 90, 90, 80) |
|---|---|---|
| `ROW_NUMBER()` | Always assigns a unique, sequential number — ties are broken arbitrarily (by whatever order the database picks if no further tiebreaker is given) | 1, 2, 3, 4 |
| `RANK()` | Ties share the same rank; the next rank **skips** ahead by the number of tied rows | 1, 2, 2, 4 |
| `DENSE_RANK()` | Ties share the same rank; the next rank does **not** skip — it's always +1 from the previous distinct rank | 1, 2, 2, 3 |

**When to use which:**
- `ROW_NUMBER()` — when you need exactly one row per group regardless of ties, e.g. picking a single "top" row (`top_employee`, `top_artist`, `segment_top_customer` in the pipeline all use this specifically because the later `CROSS JOIN`s depend on getting exactly one row back).
- `RANK()` — when ties should be visibly acknowledged and share a position, and it's acceptable (or desired) for the next position number to reflect how many rows tied — e.g. `country_ranking`, where two equally-scored countries should both legitimately be shown as rank 1, not have one arbitrarily promoted above the other.
- `DENSE_RANK()` — same tie-sharing behavior as `RANK()`, but used when you want ranks to stay **consecutive** with no gaps — useful for things like "top 3 distinct price tiers," where skipped numbers from `RANK()` would be misleading.

---

## 4. What is conditional aggregation?

Conditional aggregation means applying a `CASE WHEN` expression **inside** an aggregate function (`SUM`, `COUNT`, `AVG`, etc.) so that the aggregate only counts or sums rows meeting a condition — effectively producing multiple filtered totals in a single pass over the data, without needing separate queries or a `WHERE` clause that would exclude other columns' totals.

**Example**, extending the segment logic from this pipeline:

```sql
SELECT
    country,
    COUNT(CASE WHEN customer_segment = 'Platinum' THEN 1 END) AS platinum_customers,
    COUNT(CASE WHEN customer_segment = 'Gold' THEN 1 END) AS gold_customers,
    SUM(CASE WHEN customer_segment = 'Platinum' THEN total_amount_spent ELSE 0 END) AS platinum_revenue
FROM customer_segments_with_country
GROUP BY country;
```

This produces one row per country with separate Platinum/Gold counts and Platinum-only revenue — all computed in a single `GROUP BY country` pass, instead of running four separate filtered queries (one per segment) and stitching the results together manually.

The key mechanic: `COUNT()` ignores `NULL`s, so a `CASE WHEN condition THEN 1 END` (with no `ELSE`, which implicitly means `ELSE NULL`) only counts rows where the condition is true — everything else contributes nothing to that particular count.

---

## 5. How does CASE WHEN improve analytical reporting?

`CASE WHEN` lets raw, granular data be translated into **business-meaningful categories or labels** directly inside SQL, rather than requiring that translation to happen later in application code, a BI tool, or manually in a spreadsheet. This matters for analytical reporting in a few concrete ways:

- **Turns numbers into decisions** — `customer_segments` in this pipeline is the clearest example: raw `total_amount_spent`, `total_invoices`, and `unique_genres_purchased` numbers are meaningless to a marketing stakeholder on their own, but `CASE WHEN ... THEN 'Platinum' ...` turns them into an actionable label a non-technical audience can immediately act on.
- **Enables conditional aggregation** (see Q4) — without `CASE WHEN`, you couldn't split one column's aggregate into multiple filtered sub-aggregates in a single query.
- **Drives dynamic, personalized output** — `marketing_recommendations` uses `CASE WHEN` to select a different campaign message per segment, including interpolating a customer-specific value (`favorite_genre`) into the Silver-tier message. That's a report generating tailored content, not just tailored numbers.
- **Keeps business logic version-controlled and centralized** — because the segmentation/labeling rule lives in the SQL itself, everyone querying the same view gets the same categorization, rather than each analyst re-implementing (and potentially miscalculating) the same thresholds independently in Excel or Python.
- **Reduces the number of queries needed** — one query with several `CASE WHEN` branches often replaces what would otherwise be several near-duplicate queries filtered by `WHERE`, each returning a different slice.

---

## 6. Why should SQL queries be broken into logical stages?

Breaking a query into stages (via CTEs, as done throughout this pipeline) mirrors how a person actually reasons through an analytical problem — join the raw data, then aggregate it, then classify it, then rank it, then join the summaries together for a final report. Each stage does exactly one job.

Benefits:
- **Isolates and localizes errors.** If `country_metrics` had a double-counting bug (as caught earlier in this pipeline's development), staging made it possible to isolate exactly which step introduced the problem, rather than searching through one 100-line nested query.
- **Prevents repeated logic drifting out of sync.** Grain-sensitive calculations (like revenue at invoice-grain vs. line-item-grain) are easy to get subtly wrong when recomputed inline in multiple places. A staged pipeline computes each number once, in one place, and every downstream stage reuses that single source of truth.
- **Makes performance tuning targeted.** If one specific stage is slow, it can be indexed, materialized, or rewritten without touching the rest of the pipeline.
- **Documents intent.** Named, commented stages (`customer_purchases`, `favorite_genres`, `country_scores`, etc.) act as inline documentation of the analytical process itself — a new team member can understand *what* the query does and *why*, stage by stage, without needing a separate design document.
- **Supports incremental testing.** Each CTE can be run and validated independently (`SELECT * FROM stage_name`) before trusting the final combined output.

---

## 7. What makes a SQL query maintainable?

Pulling together the practices actually applied while building this pipeline:

1. **Logical staging with CTEs** — one clear responsibility per CTE (see Q6), rather than one dense monolithic query.
2. **Consistent, descriptive naming** — CTE and column names that describe *what* they contain (`customer_purchases`, `favorite_genres`, `country_score`) rather than vague names like `temp1` or `cte_a`.
3. **Comments explaining *why*, not just *what*** — e.g. explaining *why* `LEFT JOIN` was chosen over `INNER JOIN` to protect against future `NULL` foreign keys, not just restating that a join happened.
4. **Avoiding recomputation of the same value** — computing an aggregate once and referencing the alias downstream, rather than repeating the same `SUM(...)` expression multiple times in one query.
5. **Correct grain awareness** — being deliberate about whether you're aggregating at the invoice grain, line-item grain, or customer grain, and never blending them carelessly (the single most common source of subtle bugs across this whole pipeline).
6. **Defensive joins where data quality isn't guaranteed** — using `LEFT JOIN` (with `COALESCE` where needed) instead of `INNER JOIN` when a missing match shouldn't silently drop otherwise-valid rows.
7. **Deterministic tie-handling** — deliberately choosing `ROW_NUMBER()` vs `RANK()` vs `DENSE_RANK()` based on whether ties should be broken, shared, or gap-preserving, rather than leaving it to chance.
8. **Reusability** — wrapping a finished, stable pipeline in a `VIEW` so downstream consumers (reports, other queries, tasks 2–4 in this project) reference one canonical definition instead of copy-pasting the same logic repeatedly.
9. **Formatting consistency** — consistent indentation, keyword casing, and comma placement, so diffs in version control are clean and the query is scannable at a glance.

A maintainable query is one that a teammate — or you, six months later — can read once and trust, debug in isolation stage by stage, and extend without having to reverse-engineer the original author's intent.
