# Enterprise Analytics Hackathon — AdventureWorks Analytics Layer

**Author:** Umer Sahi

## 1. Database Overview

The source database is **AdventureWorks (OLTP)**, restored from the provided
CSV extract + `install.sql`/`instawdb.sql` scripts into PostgreSQL. It's a
transactional (not analytical) schema across five namespaces:

| Schema | Domain |
|---|---|
| `sales` | Orders, order lines, customers, salespeople, territories, stores |
| `production` | Products, categories/subcategories, inventory, locations |
| `purchasing` | Purchase orders, purchase order lines, vendors |
| `humanresources` | Employees |
| `person` | People (individuals behind customers/employees/vendors) |

**Loaded volume:** 31,465 sales orders / 121,317 order lines / 19,820
customers / 504 products / 4,012 purchase orders / 8,845 purchase order
lines / 104 vendors, spanning **2022-05-30 → 2025-06-29** (~3 years).

This is a standard, well-documented sample database (the well-known
"AdventureWorks-for-Postgres" port), so the pipeline below uses its real
table/column names throughout rather than a generic mock schema.

## 2. Analytics Architecture

Everything reusable lives in a new **`analytics` schema**, built as a strict
dependency chain (each stage is a `CREATE TABLE ... AS SELECT` that only
reads from tables built in an earlier stage — the raw operational schemas
are queried exactly once, in Stage 1/2, and never again):

```
RAW TABLES (sales / production / purchasing / person / humanresources)
        │
STAGE 1  DIMENSIONS            dim_date, dim_product, dim_customer,
                                dim_territory, dim_employee_salesperson
        │
STAGE 2  FACT TABLES           fact_sales_line, fact_purchase_line,
                                inventory_snapshot_analytics
        │
STAGE 3  DOMAIN ANALYTICS      customer_analytics, product_analytics,
                                employee_sales_analytics,
                                territory_sales_analytics,
                                vendor_purchasing_analytics
        │
STAGE 4  BUSINESS METRICS      monthly_revenue, quarterly_revenue,
                                product_performance_ranking,
                                category_performance
        │
STAGE 5  SEGMENTATION          customer_segments, customer_retention_summary
        │
STAGE 6  REGIONAL ANALYSIS     regional_performance
        │
STAGE 7  PEOPLE & OPS          salesperson_rankings, inventory_health,
                                purchasing_trends
        │
STAGE 8  EXECUTIVE KPIs        executive_monthly_kpi, executive_kpi_summary
        │
PYTHON VISUALIZATIONS  (executive_analysis.ipynb reads ONLY analytics.*)
```

**25 analytical tables/views** across **7 business domains** (customer,
product, sales, employee, territory, inventory, purchasing/vendor) — well
above the 10-table / 5-domain requirement. Row counts for every table are
listed in section 5.

`fact_sales_line` is the single source of truth for revenue/cost/margin:
every downstream table (customer, product, employee, territory, monthly,
quarterly, segmentation, regional, executive) derives its numbers from it,
so the same revenue figure is never recalculated with slightly different
logic in two places. The same principle applies to `fact_purchase_line` for
everything purchasing/vendor-related.

## 3. Intermediate Tables Created

See `analytics_pipeline.sql` for full DDL and inline comments on every
table. Summary by stage:

- **Dimensions (5):** enriched, denormalized lookups (product with category
  rollup and margin; customer resolved to a single display name whether
  they're an individual or a store account; salesperson joined to person +
  territory; a generated date spine).
- **Facts (3):** `fact_sales_line` (121,317 rows, order-line grain,
  recomputed revenue/cost/margin), `fact_purchase_line` (8,845 rows),
  `inventory_snapshot_analytics` (1,069 rows).
- **Domain analytics (5):** one row per customer / product / salesperson /
  territory / vendor, with the metrics a dashboard would need without
  re-touching the fact tables.
- **Business metrics (4):** `monthly_revenue` and `quarterly_revenue` use
  chained CTEs with `LAG()`, a 3-month moving average, and QoQ/YoY growth;
  `product_performance_ranking` uses `RANK()`, `NTILE(4)`, and `CASE WHEN`
  tagging; `category_performance` uses conditional/window aggregation for
  revenue share.
- **Segmentation (2):** `customer_segments` computes RFM scores with
  `NTILE(5)` window functions and a `CASE WHEN` segment classifier
  (Champions / Loyal / At Risk / Needs Attention / New / Lost);
  `customer_retention_summary` rolls that up per segment.
- **Regional (1):** `regional_performance` — territory revenue/margin with
  `RANK()` and full-year-only YoY growth.
- **People & ops (3):** `salesperson_rankings` (`RANK()`, `DENSE_RANK()`,
  window comparison to team average), `inventory_health` (`CASE WHEN` stock
  status vs. each product's own safety stock/reorder point),
  `purchasing_trends` (monthly spend + `LAG()`-based MoM growth).
- **Executive (2):** `executive_monthly_kpi` (wide monthly trend table) and
  `executive_kpi_summary` (single-row headline scorecard, pulling from every
  prior stage).

## 4. SQL Design Decisions

- **Recomputed, not trusted, revenue.** `fact_sales_line.line_revenue` is
  calculated from `OrderQty * UnitPrice * (1 - UnitPriceDiscount)` rather
  than copied from the raw `LineTotal` column, so there's one auditable
  formula for every downstream table. It was reconciled against
  `SalesOrderHeader.SubTotal` (off by ~$0.50 across $109.8M — pure rounding)
  before anything was built on top of it.
- **Tables, not views.** Given the chained, multi-stage design, materialized
  `CREATE TABLE AS SELECT` was used instead of plain views so each stage is
  fast to query and easy to inspect independently. In a production setting
  these would be refreshed on a schedule (see "Future work" below); the
  whole file is idempotent (`DROP TABLE IF EXISTS` before every
  `CREATE TABLE`) so it can simply be re-run.
- **Indexes** were added on every foreign-key-style join column
  (`order_date`, `customerid`, `productid`, `territoryid`, `salespersonid`,
  etc.) since these tables are meant to be queried repeatedly by a
  dashboard, not just read once.
- **Sanity checkpoints** are included after every stage (`SELECT COUNT(*)`
  summaries) so a re-run of the file surfaces a broken join immediately
  instead of silently producing an empty or malformed table.

## 5. Table Row Counts (verification snapshot)

| Table | Rows |
|---|---:|
| dim_date | 1,857 |
| dim_product | 504 |
| dim_territory | 10 |
| dim_customer | 19,820 |
| dim_employee_salesperson | 17 |
| fact_sales_line | 121,317 |
| fact_purchase_line | 8,845 |
| inventory_snapshot_analytics | 1,069 |
| customer_analytics | 19,119 |
| product_analytics | 504 |
| employee_sales_analytics | 17 |
| territory_sales_analytics | 10 |
| vendor_purchasing_analytics | 86 |
| monthly_revenue | 38 |
| quarterly_revenue | 13 |
| product_performance_ranking | 504 |
| category_performance | 4 |
| customer_segments | 19,119 |
| customer_retention_summary | 6 |
| regional_performance | 10 |
| salesperson_rankings | 17 |
| inventory_health | 432 |
| purchasing_trends | 31 |
| executive_monthly_kpi | 38 |
| executive_kpi_summary | 1 |

`customer_analytics`/`customer_segments` (19,119) is smaller than
`dim_customer` (19,820) by design — it only includes customers who placed
at least one order; the ~700 customer records with zero orders are excluded
from purchasing analytics since there's nothing to analyze about them.

## 6. Challenges Faced (and how they were resolved)

Two real data-quality issues were caught by treating every intermediate
table's output as something to sanity-check, not just trust:

1. **Partial boundary years distorting territory YoY growth.** A first pass
   at `regional_performance` compared calendar year 2025 (data through
   June only) against full-year 2024, producing nonsensical "growth" figures
   like -70%. **Fix:** `regional_performance` now only compares years where
   all 12 months are present in the data.
2. **Quota attainment compared against the wrong time window.** An early
   version of `employee_sales_analytics` divided each salesperson's
   **3-year cumulative revenue** by their **annual** `SalesQuota`, producing
   absurd "4,000% attainment" figures. **Fix:** `quota_attainment_pct` now
   compares quota against revenue in the latest complete calendar year only.
   Even after that fix, quotas ($250K–$300K) turned out to be far below
   realistic revenue per salesperson (~$2M–$4M/year) for this dataset —
   every salesperson with a quota clears it — so the notebook and
   recommendations treat that as a genuine data-limitation finding (stale
   quotas) rather than a performance story, and use team-average revenue
   comparison as the more meaningful ranking instead.

A third, milder issue: the final month in the data (June 2025) is a partial
month (905 orders vs. ~2,200 typical for surrounding months) because the
extract simply stops mid-month. `executive_kpi_summary` reports this month
separately (`partial_month_in_data`) rather than folding it into the
headline "latest month" growth figure.

## 7. Assumptions Made

- **"Customer" analytics covers purchasing customers only** — customers with
  zero historical orders are excluded from `customer_analytics` /
  `customer_segments` (they'd have undefined recency/frequency/monetary
  values otherwise).
- **Customer Lifetime Value** is a simple historical proxy (total revenue to
  date), not a predictive/discounted model — a true CLV model would need
  churn probability and time-value-of-money assumptions this dataset doesn't
  support out of the box.
- **RFM segment boundaries** (Champions / Loyal / At Risk / etc.) use a
  standard, commonly-used quintile-based scoring scheme; a real business
  would likely tune the thresholds against their own historical
  win-back/churn outcomes.
- **"Full year" for YoY comparisons** is defined as any calendar year with
  all 12 months represented in `fact_sales_line`/`monthly_revenue` — this
  automatically excludes 2022 (data starts in May) and 2025 (data ends in
  June) from year-over-year growth calculations without hardcoding specific
  years, so the logic keeps working if the pipeline is re-run against a
  larger extract later.
- **Inventory health thresholds** (`Out of Stock` / `Low Stock` /
  `Adequate` / `Healthy` / `Overstocked`) are based on each product's own
  `SafetyStockLevel` and `ReorderPoint` fields already present in
  `production.Product`, rather than an external inventory policy.

## 8. Deliverables

- `analytics_pipeline.sql` — the full, staged, commented SQL pipeline
  described above. Idempotent; safe to re-run top to bottom.
- `executive_analysis.ipynb` — connects to PostgreSQL via `psycopg2`/
  `sqlalchemy`, reads exclusively from `analytics.*` via `pandas.read_sql`,
  and produces 10 visualizations (exceeds the 8-chart requirement) each
  with a data-driven business insight underneath, followed by 5 business
  opportunities, 5 risks, and 5 recommendations — all generated
  programmatically from the live query results so they stay accurate if
  the underlying data changes.
- `README.md` — this file.

## 9. Future Work (Bonus Challenge)

Because every table in `analytics.*` only ever reads from earlier
`analytics.*` tables (never straight from the raw schemas after Stage 2), a
new dashboard can be built entirely on top of this layer. Two natural next
steps: (1) convert the `CREATE TABLE AS` statements to a scheduled job
(cron + `psql -f analytics_pipeline.sql`, or wrapped as Postgres
materialized views with `REFRESH MATERIALIZED VIEW`) so the layer updates
automatically; (2) add a lightweight `analytics.pipeline_run_log` table that
records each run's timestamp and row counts, turning the existing sanity
checkpoints into a permanent data-quality audit trail.
