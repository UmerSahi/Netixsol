-- ============================================================================
-- ENTERPRISE ANALYTICS HACKATHON — ANALYTICS PIPELINE
-- Source DB : AdventureWorks (OLTP) — schemas: person, humanresources,
--             production, purchasing, sales
-- Target    : "analytics" schema — a reusable, dashboard-ready reporting layer
-- Author    : Umer Sahi
--
-- DESIGN
--   The pipeline is a strict dependency chain:
--
--   RAW TABLES (person/production/purchasing/sales/humanresources)
--        |
--   STAGE 1  Dimensions        (dim_date, dim_product, dim_customer,
--                                dim_employee_salesperson, dim_territory)
--        |
--   STAGE 2  Fact tables        (fact_sales_line, fact_purchase_line,
--                                inventory_snapshot_analytics)
--        |
--   STAGE 3  Domain analytics   (customer_analytics, product_analytics,
--                                employee_sales_analytics,
--                                territory_sales_analytics,
--                                vendor_purchasing_analytics)
--        |
--   STAGE 4  Business metrics   (monthly_revenue, quarterly_revenue,
--                                product_performance_ranking,
--                                category_performance)
--        |
--   STAGE 5  Segmentation       (customer_segments)
--        |
--   STAGE 6  Regional analysis  (regional_performance)
--        |
--   STAGE 7  People & ops       (salesperson_rankings, inventory_health,
--                                purchasing_trends)
--        |
--   STAGE 8  Executive KPIs     (executive_kpi_summary, executive_monthly_kpi)
--        |
--   PYTHON VISUALIZATIONS (executive_analysis.ipynb reads ONLY from
--   analytics.* — never touches the raw operational schemas)
--
-- Every downstream table selects from analytics.* tables/views built in an
-- earlier stage, never re-deriving a metric that already exists upstream.
-- Run this file top-to-bottom: psql -d Adventureworks -f analytics_pipeline.sql
-- ============================================================================

DROP SCHEMA IF EXISTS analytics CASCADE;
CREATE SCHEMA analytics;
SET search_path TO analytics, public;

-- ============================================================================
-- STAGE 1 — DIMENSIONS
-- Reusable descriptive tables. Every later stage joins to these instead of
-- re-joining the raw person/production/sales tables.
-- ============================================================================

-- 1.1 DATE DIMENSION -----------------------------------------------------
-- Generated once from the min/max order dates actually present in the data,
-- padded by a year on each side so it also covers due/ship dates.
DROP TABLE IF EXISTS analytics.dim_date;
CREATE TABLE analytics.dim_date AS
WITH bounds AS (
    SELECT (MIN(orderdate) - INTERVAL '1 year')::date AS start_date,
           (MAX(orderdate) + INTERVAL '1 year')::date AS end_date
    FROM sales.salesorderheader
)
SELECT
    d::date                                   AS date_key,
    EXTRACT(YEAR FROM d)::int                  AS year,
    EXTRACT(QUARTER FROM d)::int                AS quarter,
    'Q' || EXTRACT(QUARTER FROM d)::int || ' ' || EXTRACT(YEAR FROM d)::int AS year_quarter_label,
    EXTRACT(MONTH FROM d)::int                  AS month,
    TO_CHAR(d, 'Mon')                           AS month_name,
    TO_CHAR(d, 'YYYY-MM')                       AS year_month_label,
    DATE_TRUNC('month', d)::date                AS month_start,
    DATE_TRUNC('quarter', d)::date              AS quarter_start,
    EXTRACT(DOW FROM d)::int                    AS day_of_week,
    TO_CHAR(d, 'Day')                           AS day_name
FROM bounds, generate_series(bounds.start_date, bounds.end_date, INTERVAL '1 day') AS d;

ALTER TABLE analytics.dim_date ADD PRIMARY KEY (date_key);
CREATE INDEX idx_dim_date_month ON analytics.dim_date (year, month);

-- 1.2 PRODUCT DIMENSION ---------------------------------------------------
DROP TABLE IF EXISTS analytics.dim_product;
CREATE TABLE analytics.dim_product AS
SELECT
    p.productid,
    p.name                                            AS product_name,
    p.productnumber,
    p.color,
    p.size,
    p.productline,
    p.class,
    p.style,
    p.standardcost,
    p.listprice,
    ROUND(CASE WHEN p.listprice > 0
               THEN (p.listprice - p.standardcost) / p.listprice * 100
               ELSE 0 END, 2)                          AS margin_pct,
    p.safetystocklevel,
    p.reorderpoint,
    p.daystomanufacture,
    p.sellstartdate,
    p.sellenddate,
    p.discontinueddate,
    CASE WHEN p.discontinueddate IS NOT NULL THEN FALSE
         WHEN p.sellenddate IS NOT NULL AND p.sellenddate < NOW() THEN FALSE
         ELSE TRUE END                                 AS is_active,
    sc.productsubcategoryid,
    sc.name                                            AS subcategory_name,
    cat.productcategoryid,
    cat.name                                           AS category_name
FROM production.product p
LEFT JOIN production.productsubcategory sc ON p.productsubcategoryid = sc.productsubcategoryid
LEFT JOIN production.productcategory cat ON sc.productcategoryid = cat.productcategoryid;

ALTER TABLE analytics.dim_product ADD PRIMARY KEY (productid);
CREATE INDEX idx_dim_product_cat ON analytics.dim_product (category_name, subcategory_name);

-- 1.3 TERRITORY DIMENSION -------------------------------------------------
DROP TABLE IF EXISTS analytics.dim_territory;
CREATE TABLE analytics.dim_territory AS
SELECT
    territoryid,
    name            AS territory_name,
    countryregioncode,
    "group"         AS sales_group,
    salesytd        AS territory_reported_salesytd,
    saleslastyear   AS territory_reported_saleslastyear,
    costytd,
    costlastyear
FROM sales.salesterritory;

ALTER TABLE analytics.dim_territory ADD PRIMARY KEY (territoryid);

-- 1.4 CUSTOMER DIMENSION ---------------------------------------------------
-- A customer is either an individual (PersonID set) or a store account
-- (StoreID set). Resolve a single display name either way.
DROP TABLE IF EXISTS analytics.dim_customer;
CREATE TABLE analytics.dim_customer AS
SELECT
    c.customerid,
    c.personid,
    c.storeid,
    c.territoryid,
    dt.territory_name,
    dt.sales_group,
    CASE
        WHEN c.storeid IS NOT NULL THEN 'Store'
        ELSE 'Individual'
    END                                              AS customer_type,
    CASE
        WHEN c.storeid IS NOT NULL THEN st.name
        WHEN c.personid IS NOT NULL THEN TRIM(pe.firstname || ' ' || COALESCE(pe.middlename || ' ', '') || pe.lastname)
        ELSE 'Unknown Customer'
    END                                              AS customer_name,
    c.modifieddate                                   AS customer_modified_date
FROM sales.customer c
LEFT JOIN person.person pe ON c.personid = pe.businessentityid
LEFT JOIN sales.store st ON c.storeid = st.businessentityid
LEFT JOIN analytics.dim_territory dt ON c.territoryid = dt.territoryid;

ALTER TABLE analytics.dim_customer ADD PRIMARY KEY (customerid);
CREATE INDEX idx_dim_customer_territory ON analytics.dim_customer (territoryid);

-- 1.5 SALESPERSON (EMPLOYEE) DIMENSION ------------------------------------
DROP TABLE IF EXISTS analytics.dim_employee_salesperson;
CREATE TABLE analytics.dim_employee_salesperson AS
SELECT
    sp.businessentityid                              AS salespersonid,
    TRIM(pe.firstname || ' ' || COALESCE(pe.middlename || ' ', '') || pe.lastname) AS salesperson_name,
    e.jobtitle,
    e.hiredate,
    sp.territoryid,
    dt.territory_name,
    sp.salesquota,
    sp.bonus,
    sp.commissionpct,
    sp.salesytd                                      AS reported_salesytd,
    sp.saleslastyear                                 AS reported_saleslastyear
FROM sales.salesperson sp
JOIN humanresources.employee e ON sp.businessentityid = e.businessentityid
JOIN person.person pe ON sp.businessentityid = pe.businessentityid
LEFT JOIN analytics.dim_territory dt ON sp.territoryid = dt.territoryid;

ALTER TABLE analytics.dim_employee_salesperson ADD PRIMARY KEY (salespersonid);

-- Sanity checkpoint for Stage 1
SELECT 'dim_date' AS tbl, COUNT(*) FROM analytics.dim_date
UNION ALL SELECT 'dim_product', COUNT(*) FROM analytics.dim_product
UNION ALL SELECT 'dim_territory', COUNT(*) FROM analytics.dim_territory
UNION ALL SELECT 'dim_customer', COUNT(*) FROM analytics.dim_customer
UNION ALL SELECT 'dim_employee_salesperson', COUNT(*) FROM analytics.dim_employee_salesperson;


-- ============================================================================
-- STAGE 2 — FACT TABLES
-- The single reusable, line-grain source of truth for revenue/cost/margin.
-- Every sales metric downstream (customer, product, employee, territory,
-- monthly/quarterly, segmentation, regional, executive KPIs) is derived
-- from fact_sales_line so the same revenue number is never recalculated
-- with slightly different logic in two places.
-- ============================================================================

-- 2.1 SALES FACT (order-line grain) ---------------------------------------
DROP TABLE IF EXISTS analytics.fact_sales_line;
CREATE TABLE analytics.fact_sales_line AS
SELECT
    sod.salesorderid,
    sod.salesorderdetailid,
    soh.orderdate::date                                        AS order_date,
    soh.duedate::date                                          AS due_date,
    soh.shipdate::date                                         AS ship_date,
    soh.onlineorderflag                                        AS is_online_order,
    soh.customerid,
    dc.customer_name,
    dc.customer_type,
    soh.salespersonid,
    des.salesperson_name,
    soh.territoryid,
    dt.territory_name,
    dt.sales_group,
    sod.productid,
    dp.product_name,
    dp.category_name,
    dp.subcategory_name,
    sod.orderqty,
    sod.unitprice,
    sod.unitpricediscount,
    dp.standardcost                                            AS unit_standard_cost,
    -- Revenue is recomputed here (once) from unitprice/discount/qty rather
    -- than trusted verbatim from the raw LineTotal column, so every
    -- downstream table inherits one single, auditable revenue formula.
    ROUND(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount), 2) AS line_revenue,
    ROUND(sod.orderqty * dp.standardcost, 2)                    AS line_cost,
    ROUND(sod.orderqty * sod.unitprice * (1 - sod.unitpricediscount)
          - sod.orderqty * dp.standardcost, 2)                  AS line_margin
FROM sales.salesorderdetail sod
JOIN sales.salesorderheader soh ON sod.salesorderid = soh.salesorderid
JOIN analytics.dim_product dp ON sod.productid = dp.productid
LEFT JOIN analytics.dim_customer dc ON soh.customerid = dc.customerid
LEFT JOIN analytics.dim_employee_salesperson des ON soh.salespersonid = des.salespersonid
LEFT JOIN analytics.dim_territory dt ON soh.territoryid = dt.territoryid;

ALTER TABLE analytics.fact_sales_line ADD PRIMARY KEY (salesorderdetailid);
CREATE INDEX idx_fact_sales_date ON analytics.fact_sales_line (order_date);
CREATE INDEX idx_fact_sales_customer ON analytics.fact_sales_line (customerid);
CREATE INDEX idx_fact_sales_product ON analytics.fact_sales_line (productid);
CREATE INDEX idx_fact_sales_territory ON analytics.fact_sales_line (territoryid);
CREATE INDEX idx_fact_sales_salesperson ON analytics.fact_sales_line (salespersonid);

-- 2.2 PURCHASING FACT (PO line grain) --------------------------------------
DROP TABLE IF EXISTS analytics.fact_purchase_line;
CREATE TABLE analytics.fact_purchase_line AS
SELECT
    pod.purchaseorderid,
    pod.purchaseorderdetailid,
    poh.orderdate::date                                        AS order_date,
    poh.shipdate::date                                         AS ship_date,
    poh.status                                                  AS po_status,
    poh.vendorid,
    v.name                                                      AS vendor_name,
    v.creditrating,
    v.preferredvendorstatus,
    pod.productid,
    dp.product_name,
    dp.category_name,
    pod.orderqty,
    pod.unitprice,
    ROUND(pod.orderqty * pod.unitprice, 2)                      AS line_cost,
    pod.receivedqty,
    pod.rejectedqty,
    pod.duedate::date                                           AS due_date,
    CASE WHEN poh.shipdate IS NOT NULL
         THEN (poh.shipdate::date - poh.orderdate::date)
         ELSE NULL END                                          AS lead_time_days
FROM purchasing.purchaseorderdetail pod
JOIN purchasing.purchaseorderheader poh ON pod.purchaseorderid = poh.purchaseorderid
JOIN purchasing.vendor v ON poh.vendorid = v.businessentityid
JOIN analytics.dim_product dp ON pod.productid = dp.productid;

ALTER TABLE analytics.fact_purchase_line ADD PRIMARY KEY (purchaseorderdetailid);
CREATE INDEX idx_fact_purchase_date ON analytics.fact_purchase_line (order_date);
CREATE INDEX idx_fact_purchase_vendor ON analytics.fact_purchase_line (vendorid);
CREATE INDEX idx_fact_purchase_product ON analytics.fact_purchase_line (productid);

-- 2.3 INVENTORY SNAPSHOT ----------------------------------------------------
DROP TABLE IF EXISTS analytics.inventory_snapshot_analytics;
CREATE TABLE analytics.inventory_snapshot_analytics AS
SELECT
    pi.productid,
    dp.product_name,
    dp.category_name,
    dp.subcategory_name,
    pi.locationid,
    l.name                                                      AS location_name,
    pi.quantity                                                 AS qty_on_hand,
    dp.safetystocklevel,
    dp.reorderpoint,
    dp.standardcost,
    ROUND(pi.quantity * dp.standardcost, 2)                     AS inventory_value
FROM production.productinventory pi
JOIN analytics.dim_product dp ON pi.productid = dp.productid
JOIN production.location l ON pi.locationid = l.locationid;

CREATE INDEX idx_inv_snap_product ON analytics.inventory_snapshot_analytics (productid);

-- Sanity checkpoint for Stage 2
SELECT 'fact_sales_line' AS tbl, COUNT(*) FROM analytics.fact_sales_line
UNION ALL SELECT 'fact_purchase_line', COUNT(*) FROM analytics.fact_purchase_line
UNION ALL SELECT 'inventory_snapshot_analytics', COUNT(*) FROM analytics.inventory_snapshot_analytics;

-- Revenue reconciliation: fact_sales_line total should match sum(SalesOrderHeader.SubTotal)
-- within rounding, confirming the recomputed formula is correct.
SELECT
    ROUND(SUM(line_revenue), 2) AS fact_table_revenue,
    (SELECT ROUND(SUM(subtotal), 2) FROM sales.salesorderheader) AS header_subtotal_revenue
FROM analytics.fact_sales_line;


-- ============================================================================
-- STAGE 3 — DOMAIN ANALYTICS (built entirely from fact_sales_line /
-- fact_purchase_line — never re-touches the raw sales/production tables)
-- ============================================================================

-- 3.1 CUSTOMER ANALYTICS ----------------------------------------------------
DROP TABLE IF EXISTS analytics.customer_analytics;
CREATE TABLE analytics.customer_analytics AS
WITH order_level AS (
    -- Collapse line-grain fact to one row per order first, so an order
    -- with many line items is counted once for order-count metrics.
    SELECT
        customerid,
        salesorderid,
        MIN(order_date)  AS order_date,
        SUM(line_revenue) AS order_revenue
    FROM analytics.fact_sales_line
    GROUP BY customerid, salesorderid
)
SELECT
    dc.customerid,
    dc.customer_name,
    dc.customer_type,
    dc.territory_name,
    COUNT(DISTINCT ol.salesorderid)                    AS total_orders,
    ROUND(SUM(ol.order_revenue), 2)                     AS total_revenue,
    ROUND(AVG(ol.order_revenue), 2)                      AS avg_order_value,
    MIN(ol.order_date)                                   AS first_order_date,
    MAX(ol.order_date)                                   AS last_order_date,
    (SELECT MAX(order_date) FROM analytics.fact_sales_line) - MAX(ol.order_date) AS days_since_last_order
FROM order_level ol
JOIN analytics.dim_customer dc ON ol.customerid = dc.customerid
GROUP BY dc.customerid, dc.customer_name, dc.customer_type, dc.territory_name;

ALTER TABLE analytics.customer_analytics ADD PRIMARY KEY (customerid);

-- 3.2 PRODUCT ANALYTICS ------------------------------------------------------
DROP TABLE IF EXISTS analytics.product_analytics;
CREATE TABLE analytics.product_analytics AS
SELECT
    dp.productid,
    dp.product_name,
    dp.category_name,
    dp.subcategory_name,
    dp.listprice,
    dp.standardcost,
    dp.margin_pct                                        AS list_margin_pct,
    dp.is_active,
    COUNT(DISTINCT f.salesorderid)                        AS order_count,
    COALESCE(SUM(f.orderqty), 0)                          AS units_sold,
    ROUND(COALESCE(SUM(f.line_revenue), 0), 2)             AS total_revenue,
    ROUND(COALESCE(SUM(f.line_cost), 0), 2)                 AS total_cost,
    ROUND(COALESCE(SUM(f.line_margin), 0), 2)                AS total_margin,
    ROUND(CASE WHEN SUM(f.line_revenue) > 0
               THEN SUM(f.line_margin) / SUM(f.line_revenue) * 100
               ELSE 0 END, 2)                              AS realized_margin_pct
FROM analytics.dim_product dp
LEFT JOIN analytics.fact_sales_line f ON dp.productid = f.productid
GROUP BY dp.productid, dp.product_name, dp.category_name, dp.subcategory_name,
         dp.listprice, dp.standardcost, dp.margin_pct, dp.is_active;

ALTER TABLE analytics.product_analytics ADD PRIMARY KEY (productid);

-- 3.3 EMPLOYEE (SALESPERSON) ANALYTICS --------------------------------------
-- NOTE: SalesPerson.SalesQuota is an annual figure, so it must never be
-- compared against multi-year cumulative revenue (that would inflate
-- "attainment" to 2,000-4,000%, as an early build of this table did).
-- quota_attainment_pct here instead compares quota against each
-- salesperson's revenue in the latest COMPLETE calendar year on record.
DROP TABLE IF EXISTS analytics.employee_sales_analytics;
CREATE TABLE analytics.employee_sales_analytics AS
WITH full_years AS (
    SELECT EXTRACT(YEAR FROM order_date)::int AS year
    FROM analytics.fact_sales_line
    GROUP BY EXTRACT(YEAR FROM order_date)
    HAVING COUNT(DISTINCT DATE_TRUNC('month', order_date)) = 12
),
latest_full_year AS (
    SELECT MAX(year) AS year FROM full_years
),
latest_year_revenue AS (
    SELECT
        f.salespersonid,
        ROUND(SUM(f.line_revenue), 2) AS latest_full_year_revenue
    FROM analytics.fact_sales_line f, latest_full_year lfy
    WHERE EXTRACT(YEAR FROM f.order_date)::int = lfy.year
    GROUP BY f.salespersonid
)
SELECT
    des.salespersonid,
    des.salesperson_name,
    des.jobtitle,
    des.territory_name,
    des.salesquota,
    des.commissionpct,
    COUNT(DISTINCT f.salesorderid)                        AS order_count,
    COUNT(DISTINCT f.customerid)                          AS customers_served,
    ROUND(COALESCE(SUM(f.line_revenue), 0), 2)             AS total_revenue,
    (SELECT lfy.year FROM latest_full_year lfy)            AS quota_comparison_year,
    COALESCE(lyr.latest_full_year_revenue, 0)              AS latest_full_year_revenue,
    ROUND(CASE WHEN des.salesquota > 0
               THEN COALESCE(lyr.latest_full_year_revenue, 0) / des.salesquota * 100
               ELSE NULL END, 2)                            AS quota_attainment_pct
FROM analytics.dim_employee_salesperson des
LEFT JOIN analytics.fact_sales_line f ON des.salespersonid = f.salespersonid
LEFT JOIN latest_year_revenue lyr ON des.salespersonid = lyr.salespersonid
GROUP BY des.salespersonid, des.salesperson_name, des.jobtitle, des.territory_name,
         des.salesquota, des.commissionpct, lyr.latest_full_year_revenue;

ALTER TABLE analytics.employee_sales_analytics ADD PRIMARY KEY (salespersonid);

-- 3.4 TERRITORY ANALYTICS -----------------------------------------------------
DROP TABLE IF EXISTS analytics.territory_sales_analytics;
CREATE TABLE analytics.territory_sales_analytics AS
SELECT
    dt.territoryid,
    dt.territory_name,
    dt.countryregioncode,
    dt.sales_group,
    COUNT(DISTINCT f.salesorderid)                        AS order_count,
    COUNT(DISTINCT f.customerid)                          AS customer_count,
    COUNT(DISTINCT f.salespersonid)                       AS salesperson_count,
    ROUND(COALESCE(SUM(f.line_revenue), 0), 2)             AS total_revenue,
    ROUND(COALESCE(SUM(f.line_margin), 0), 2)               AS total_margin
FROM analytics.dim_territory dt
LEFT JOIN analytics.fact_sales_line f ON dt.territoryid = f.territoryid
GROUP BY dt.territoryid, dt.territory_name, dt.countryregioncode, dt.sales_group;

ALTER TABLE analytics.territory_sales_analytics ADD PRIMARY KEY (territoryid);

-- 3.5 VENDOR / PURCHASING ANALYTICS -------------------------------------------
DROP TABLE IF EXISTS analytics.vendor_purchasing_analytics;
CREATE TABLE analytics.vendor_purchasing_analytics AS
SELECT
    vendorid,
    vendor_name,
    MAX(creditrating)                                     AS credit_rating,
    BOOL_OR(preferredvendorstatus)                        AS is_preferred,
    COUNT(DISTINCT purchaseorderid)                        AS po_count,
    SUM(orderqty)                                          AS total_qty_ordered,
    ROUND(SUM(line_cost), 2)                                AS total_spend,
    ROUND(AVG(lead_time_days), 1)                            AS avg_lead_time_days,
    ROUND(SUM(rejectedqty), 2)                               AS total_rejected_qty,
    ROUND(CASE WHEN SUM(receivedqty) > 0
               THEN SUM(rejectedqty) / SUM(receivedqty) * 100
               ELSE 0 END, 2)                                AS rejection_rate_pct
FROM analytics.fact_purchase_line
GROUP BY vendorid, vendor_name;

ALTER TABLE analytics.vendor_purchasing_analytics ADD PRIMARY KEY (vendorid);

-- Sanity checkpoint for Stage 3
SELECT 'customer_analytics' AS tbl, COUNT(*) FROM analytics.customer_analytics
UNION ALL SELECT 'product_analytics', COUNT(*) FROM analytics.product_analytics
UNION ALL SELECT 'employee_sales_analytics', COUNT(*) FROM analytics.employee_sales_analytics
UNION ALL SELECT 'territory_sales_analytics', COUNT(*) FROM analytics.territory_sales_analytics
UNION ALL SELECT 'vendor_purchasing_analytics', COUNT(*) FROM analytics.vendor_purchasing_analytics;


-- ============================================================================
-- STAGE 4 — BUSINESS METRICS (time series + rankings)
-- Built on fact_sales_line (for the raw revenue grain) and product_analytics
-- (for rankings, so unit/revenue totals are not recomputed).
-- ============================================================================

-- 4.1 MONTHLY REVENUE (chained CTE + window function MoM growth) -----------
DROP TABLE IF EXISTS analytics.monthly_revenue;
CREATE TABLE analytics.monthly_revenue AS
WITH monthly AS (
    SELECT
        DATE_TRUNC('month', order_date)::date AS month_start,
        TO_CHAR(order_date, 'YYYY-MM')         AS year_month_label,
        COUNT(DISTINCT salesorderid)           AS order_count,
        COUNT(DISTINCT customerid)             AS customer_count,
        ROUND(SUM(line_revenue), 2)            AS revenue,
        ROUND(SUM(line_margin), 2)             AS margin
    FROM analytics.fact_sales_line
    GROUP BY DATE_TRUNC('month', order_date), TO_CHAR(order_date, 'YYYY-MM')
)
SELECT
    month_start,
    year_month_label,
    order_count,
    customer_count,
    revenue,
    margin,
    LAG(revenue) OVER (ORDER BY month_start)                       AS prev_month_revenue,
    ROUND(revenue - LAG(revenue) OVER (ORDER BY month_start), 2)   AS mom_revenue_change,
    ROUND(CASE WHEN LAG(revenue) OVER (ORDER BY month_start) > 0
               THEN (revenue - LAG(revenue) OVER (ORDER BY month_start))
                    / LAG(revenue) OVER (ORDER BY month_start) * 100
               ELSE NULL END, 2)                                   AS mom_growth_pct,
    ROUND(AVG(revenue) OVER (ORDER BY month_start
              ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)         AS revenue_3mo_moving_avg
FROM monthly
ORDER BY month_start;

ALTER TABLE analytics.monthly_revenue ADD PRIMARY KEY (month_start);

-- 4.2 QUARTERLY REVENUE (chained CTE + window functions: QoQ and YoY) -------
DROP TABLE IF EXISTS analytics.quarterly_revenue;
CREATE TABLE analytics.quarterly_revenue AS
WITH quarterly AS (
    SELECT
        DATE_TRUNC('quarter', order_date)::date AS quarter_start,
        EXTRACT(YEAR FROM order_date)::int       AS year,
        EXTRACT(QUARTER FROM order_date)::int    AS quarter,
        COUNT(DISTINCT salesorderid)             AS order_count,
        ROUND(SUM(line_revenue), 2)              AS revenue,
        ROUND(SUM(line_margin), 2)               AS margin
    FROM analytics.fact_sales_line
    GROUP BY DATE_TRUNC('quarter', order_date), EXTRACT(YEAR FROM order_date), EXTRACT(QUARTER FROM order_date)
)
SELECT
    quarter_start,
    'Q' || quarter || ' ' || year                                   AS year_quarter_label,
    year,
    quarter,
    order_count,
    revenue,
    margin,
    LAG(revenue) OVER (ORDER BY quarter_start)                       AS prev_quarter_revenue,
    ROUND(CASE WHEN LAG(revenue) OVER (ORDER BY quarter_start) > 0
               THEN (revenue - LAG(revenue) OVER (ORDER BY quarter_start))
                    / LAG(revenue) OVER (ORDER BY quarter_start) * 100
               ELSE NULL END, 2)                                    AS qoq_growth_pct,
    LAG(revenue, 4) OVER (ORDER BY quarter_start)                    AS revenue_same_quarter_last_year,
    ROUND(CASE WHEN LAG(revenue, 4) OVER (ORDER BY quarter_start) > 0
               THEN (revenue - LAG(revenue, 4) OVER (ORDER BY quarter_start))
                    / LAG(revenue, 4) OVER (ORDER BY quarter_start) * 100
               ELSE NULL END, 2)                                    AS yoy_growth_pct
FROM quarterly
ORDER BY quarter_start;

ALTER TABLE analytics.quarterly_revenue ADD PRIMARY KEY (quarter_start);

-- 4.3 PRODUCT PERFORMANCE RANKING (ranking functions on product_analytics) --
DROP TABLE IF EXISTS analytics.product_performance_ranking;
CREATE TABLE analytics.product_performance_ranking AS
SELECT
    productid,
    product_name,
    category_name,
    subcategory_name,
    units_sold,
    total_revenue,
    total_margin,
    realized_margin_pct,
    RANK() OVER (ORDER BY total_revenue DESC)                        AS revenue_rank_overall,
    RANK() OVER (PARTITION BY category_name ORDER BY total_revenue DESC) AS revenue_rank_in_category,
    NTILE(4) OVER (ORDER BY total_revenue DESC)                      AS revenue_quartile,
    CASE
        WHEN total_revenue = 0 THEN 'No Sales'
        WHEN RANK() OVER (ORDER BY total_revenue DESC) <= 10 THEN 'Top 10 Seller'
        WHEN RANK() OVER (ORDER BY total_revenue ASC) <= 10 THEN 'Bottom 10 Seller'
        ELSE 'Mid Performer'
    END                                                                AS performance_tag
FROM analytics.product_analytics
ORDER BY total_revenue DESC;

ALTER TABLE analytics.product_performance_ranking ADD PRIMARY KEY (productid);

-- 4.4 CATEGORY PERFORMANCE ---------------------------------------------------
DROP TABLE IF EXISTS analytics.category_performance;
CREATE TABLE analytics.category_performance AS
SELECT
    category_name,
    COUNT(*)                                            AS product_count,
    SUM(units_sold)                                      AS units_sold,
    ROUND(SUM(total_revenue), 2)                          AS total_revenue,
    ROUND(SUM(total_margin), 2)                            AS total_margin,
    ROUND(CASE WHEN SUM(total_revenue) > 0
               THEN SUM(total_margin) / SUM(total_revenue) * 100
               ELSE 0 END, 2)                              AS realized_margin_pct,
    ROUND(100.0 * SUM(total_revenue) / NULLIF(SUM(SUM(total_revenue)) OVER (), 0), 2) AS pct_of_total_revenue
FROM analytics.product_analytics
WHERE category_name IS NOT NULL
GROUP BY category_name
ORDER BY total_revenue DESC;

-- Sanity checkpoint for Stage 4
SELECT 'monthly_revenue' AS tbl, COUNT(*) FROM analytics.monthly_revenue
UNION ALL SELECT 'quarterly_revenue', COUNT(*) FROM analytics.quarterly_revenue
UNION ALL SELECT 'product_performance_ranking', COUNT(*) FROM analytics.product_performance_ranking
UNION ALL SELECT 'category_performance', COUNT(*) FROM analytics.category_performance;


-- ============================================================================
-- STAGE 5 — CUSTOMER SEGMENTATION (RFM, built entirely from customer_analytics)
-- ============================================================================

DROP TABLE IF EXISTS analytics.customer_segments;
CREATE TABLE analytics.customer_segments AS
WITH rfm_scores AS (
    SELECT
        customerid,
        customer_name,
        customer_type,
        territory_name,
        total_orders,
        total_revenue,
        avg_order_value,
        first_order_date,
        last_order_date,
        days_since_last_order,
        -- Lower recency (fewer days since last order) = better = higher score.
        NTILE(5) OVER (ORDER BY days_since_last_order DESC) AS recency_score,
        NTILE(5) OVER (ORDER BY total_orders ASC)            AS frequency_score,
        NTILE(5) OVER (ORDER BY total_revenue ASC)           AS monetary_score
    FROM analytics.customer_analytics
)
SELECT
    customerid,
    customer_name,
    customer_type,
    territory_name,
    total_orders,
    total_revenue,
    avg_order_value,
    first_order_date,
    last_order_date,
    days_since_last_order,
    recency_score,
    frequency_score,
    monetary_score,
    (recency_score + frequency_score + monetary_score)                    AS rfm_total_score,
    CASE WHEN total_orders > 1 THEN TRUE ELSE FALSE END                    AS is_repeat_customer,
    -- Simple historical CLV proxy: total revenue to date. (True predictive
    -- CLV would need a churn/lifetime model — flagged as an assumption
    -- in the README.)
    total_revenue                                                          AS customer_lifetime_value_to_date,
    CASE
        WHEN recency_score >= 4 AND frequency_score >= 4 AND monetary_score >= 4 THEN 'Champions'
        WHEN recency_score >= 3 AND frequency_score >= 3                          THEN 'Loyal Customers'
        WHEN recency_score >= 4 AND frequency_score <= 2                          THEN 'New / Promising'
        WHEN recency_score <= 2 AND frequency_score >= 4                          THEN 'At Risk (was loyal)'
        WHEN recency_score <= 2 AND frequency_score <= 2 AND monetary_score <= 2  THEN 'Lost / Churned'
        ELSE 'Needs Attention'
    END                                                                     AS customer_segment
FROM rfm_scores;

ALTER TABLE analytics.customer_segments ADD PRIMARY KEY (customerid);
CREATE INDEX idx_cust_segments_segment ON analytics.customer_segments (customer_segment);

-- Repeat customer / retention summary (built from customer_segments)
DROP TABLE IF EXISTS analytics.customer_retention_summary;
CREATE TABLE analytics.customer_retention_summary AS
SELECT
    customer_segment,
    COUNT(*)                                            AS customer_count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)    AS pct_of_customers,
    ROUND(SUM(total_revenue), 2)                          AS segment_revenue,
    ROUND(100.0 * SUM(total_revenue) / NULLIF(SUM(SUM(total_revenue)) OVER (), 0), 2) AS pct_of_revenue,
    ROUND(AVG(total_orders), 2)                            AS avg_orders_per_customer,
    SUM(CASE WHEN is_repeat_customer THEN 1 ELSE 0 END)   AS repeat_customer_count
FROM analytics.customer_segments
GROUP BY customer_segment
ORDER BY segment_revenue DESC;

-- Sanity checkpoint for Stage 5
SELECT 'customer_segments' AS tbl, COUNT(*) FROM analytics.customer_segments
UNION ALL SELECT 'customer_retention_summary', COUNT(*) FROM analytics.customer_retention_summary;


-- ============================================================================
-- STAGE 6 — REGIONAL ANALYSIS (built from territory_sales_analytics +
-- monthly_revenue-style growth logic applied per territory)
-- ============================================================================

DROP TABLE IF EXISTS analytics.regional_performance;
CREATE TABLE analytics.regional_performance AS
WITH full_years AS (
    -- Only calendar years with all 12 months present in the data qualify
    -- for YoY comparison; the dataset's first (2022) and last (2025) years
    -- are partial and would otherwise distort growth figures.
    SELECT EXTRACT(YEAR FROM month_start)::int AS year
    FROM analytics.monthly_revenue
    GROUP BY EXTRACT(YEAR FROM month_start)
    HAVING COUNT(*) = 12
),
territory_yearly AS (
    SELECT
        f.territoryid,
        EXTRACT(YEAR FROM f.order_date)::int AS year,
        SUM(f.line_revenue)                   AS revenue
    FROM analytics.fact_sales_line f
    WHERE f.territoryid IS NOT NULL
      AND EXTRACT(YEAR FROM f.order_date)::int IN (SELECT year FROM full_years)
    GROUP BY f.territoryid, EXTRACT(YEAR FROM f.order_date)
),
territory_growth AS (
    SELECT
        territoryid,
        year,
        revenue,
        LAG(revenue) OVER (PARTITION BY territoryid ORDER BY year) AS prev_year_revenue,
        ROUND(CASE WHEN LAG(revenue) OVER (PARTITION BY territoryid ORDER BY year) > 0
                   THEN (revenue - LAG(revenue) OVER (PARTITION BY territoryid ORDER BY year))
                        / LAG(revenue) OVER (PARTITION BY territoryid ORDER BY year) * 100
                   ELSE NULL END, 2) AS yoy_growth_pct
    FROM territory_yearly
),
latest_growth AS (
    -- Most recent year with a computable YoY figure, per territory
    SELECT DISTINCT ON (territoryid)
        territoryid, year, yoy_growth_pct
    FROM territory_growth
    WHERE yoy_growth_pct IS NOT NULL
    ORDER BY territoryid, year DESC
)
SELECT
    tsa.territoryid,
    tsa.territory_name,
    tsa.countryregioncode,
    tsa.sales_group,
    tsa.order_count,
    tsa.customer_count,
    tsa.salesperson_count,
    tsa.total_revenue,
    tsa.total_margin,
    ROUND(CASE WHEN tsa.total_revenue > 0
               THEN tsa.total_margin / tsa.total_revenue * 100 ELSE 0 END, 2) AS margin_pct,
    RANK() OVER (ORDER BY tsa.total_revenue DESC)                    AS revenue_rank,
    lg.year                                                            AS latest_growth_year,
    lg.yoy_growth_pct                                                  AS latest_yoy_growth_pct,
    CASE
        WHEN RANK() OVER (ORDER BY tsa.total_revenue DESC) <= 3 THEN 'Top Territory'
        WHEN RANK() OVER (ORDER BY tsa.total_revenue ASC) <= 3  THEN 'Lowest Performing'
        ELSE 'Mid-Tier'
    END                                                                 AS territory_tier
FROM analytics.territory_sales_analytics tsa
LEFT JOIN latest_growth lg ON tsa.territoryid = lg.territoryid
ORDER BY tsa.total_revenue DESC;

ALTER TABLE analytics.regional_performance ADD PRIMARY KEY (territoryid);

-- Sanity checkpoint for Stage 6
SELECT * FROM analytics.regional_performance ORDER BY total_revenue DESC;


-- ============================================================================
-- STAGE 7 — PEOPLE & OPERATIONS ANALYTICS
-- ============================================================================

-- 7.1 SALESPERSON RANKINGS (built from employee_sales_analytics) -----------
DROP TABLE IF EXISTS analytics.salesperson_rankings;
CREATE TABLE analytics.salesperson_rankings AS
SELECT
    salespersonid,
    salesperson_name,
    jobtitle,
    territory_name,
    order_count,
    customers_served,
    total_revenue,
    salesquota,
    quota_comparison_year,
    latest_full_year_revenue,
    quota_attainment_pct,
    RANK() OVER (ORDER BY total_revenue DESC)                 AS revenue_rank,
    DENSE_RANK() OVER (ORDER BY quota_attainment_pct DESC NULLS LAST) AS quota_rank,
    ROUND(total_revenue - AVG(total_revenue) OVER (), 2)       AS revenue_vs_team_avg,
    CASE
        WHEN quota_attainment_pct IS NULL THEN 'No Quota Set'
        WHEN quota_attainment_pct >= 100 THEN 'Exceeds Quota'
        WHEN quota_attainment_pct >= 80  THEN 'Near Quota'
        ELSE 'Below Quota'
    END                                                          AS performance_tier
FROM analytics.employee_sales_analytics
ORDER BY total_revenue DESC;

ALTER TABLE analytics.salesperson_rankings ADD PRIMARY KEY (salespersonid);

-- 7.2 INVENTORY HEALTH (built from inventory_snapshot_analytics) -----------
DROP TABLE IF EXISTS analytics.inventory_health;
CREATE TABLE analytics.inventory_health AS
SELECT
    productid,
    product_name,
    category_name,
    subcategory_name,
    SUM(qty_on_hand)                                          AS total_qty_on_hand,
    MAX(safetystocklevel)                                      AS safety_stock_level,
    MAX(reorderpoint)                                          AS reorder_point,
    ROUND(SUM(inventory_value), 2)                              AS total_inventory_value,
    CASE
        WHEN SUM(qty_on_hand) = 0 THEN 'Out of Stock'
        WHEN SUM(qty_on_hand) <= MAX(reorderpoint) THEN 'Low Stock (At/Below Reorder Point)'
        WHEN SUM(qty_on_hand) <= MAX(safetystocklevel) THEN 'Adequate'
        WHEN SUM(qty_on_hand) > MAX(safetystocklevel) * 3 THEN 'Overstocked'
        ELSE 'Healthy'
    END                                                          AS stock_status
FROM analytics.inventory_snapshot_analytics
GROUP BY productid, product_name, category_name, subcategory_name;

ALTER TABLE analytics.inventory_health ADD PRIMARY KEY (productid);
CREATE INDEX idx_inv_health_status ON analytics.inventory_health (stock_status);

-- 7.3 PURCHASING TRENDS (monthly, built from fact_purchase_line) -----------
DROP TABLE IF EXISTS analytics.purchasing_trends;
CREATE TABLE analytics.purchasing_trends AS
WITH monthly_purchasing AS (
    SELECT
        DATE_TRUNC('month', order_date)::date AS month_start,
        TO_CHAR(order_date, 'YYYY-MM')         AS year_month_label,
        COUNT(DISTINCT purchaseorderid)        AS po_count,
        SUM(orderqty)                           AS total_qty_ordered,
        ROUND(SUM(line_cost), 2)                 AS total_spend,
        ROUND(AVG(lead_time_days), 1)             AS avg_lead_time_days
    FROM analytics.fact_purchase_line
    GROUP BY DATE_TRUNC('month', order_date), TO_CHAR(order_date, 'YYYY-MM')
)
SELECT
    month_start,
    year_month_label,
    po_count,
    total_qty_ordered,
    total_spend,
    avg_lead_time_days,
    ROUND(total_spend - LAG(total_spend) OVER (ORDER BY month_start), 2) AS mom_spend_change,
    ROUND(CASE WHEN LAG(total_spend) OVER (ORDER BY month_start) > 0
               THEN (total_spend - LAG(total_spend) OVER (ORDER BY month_start))
                    / LAG(total_spend) OVER (ORDER BY month_start) * 100
               ELSE NULL END, 2)                                          AS mom_spend_growth_pct
FROM monthly_purchasing
ORDER BY month_start;

ALTER TABLE analytics.purchasing_trends ADD PRIMARY KEY (month_start);

-- Sanity checkpoint for Stage 7
SELECT 'salesperson_rankings' AS tbl, COUNT(*) FROM analytics.salesperson_rankings
UNION ALL SELECT 'inventory_health', COUNT(*) FROM analytics.inventory_health
UNION ALL SELECT 'purchasing_trends', COUNT(*) FROM analytics.purchasing_trends;

SELECT stock_status, COUNT(*) FROM analytics.inventory_health GROUP BY stock_status ORDER BY 2 DESC;


-- ============================================================================
-- STAGE 8 — EXECUTIVE KPI DATASETS
-- The final, dashboard-ready layer. Every value here is pulled from a table
-- built in an earlier stage — nothing is recalculated from raw data again.
-- ============================================================================

-- 8.1 EXECUTIVE MONTHLY KPI (single wide table for a trend dashboard) ------
DROP TABLE IF EXISTS analytics.executive_monthly_kpi;
CREATE TABLE analytics.executive_monthly_kpi AS
SELECT
    mr.month_start,
    mr.year_month_label,
    mr.order_count,
    mr.customer_count,
    mr.revenue,
    mr.margin,
    ROUND(CASE WHEN mr.revenue > 0 THEN mr.margin / mr.revenue * 100 ELSE 0 END, 2) AS margin_pct,
    mr.mom_growth_pct,
    mr.revenue_3mo_moving_avg,
    pt.po_count                                             AS purchase_order_count,
    pt.total_spend                                          AS purchasing_spend,
    pt.avg_lead_time_days
FROM analytics.monthly_revenue mr
LEFT JOIN analytics.purchasing_trends pt ON mr.month_start = pt.month_start
ORDER BY mr.month_start;

ALTER TABLE analytics.executive_monthly_kpi ADD PRIMARY KEY (month_start);

-- 8.2 EXECUTIVE KPI SUMMARY (single-row headline scorecard) ----------------
DROP TABLE IF EXISTS analytics.executive_kpi_summary;
CREATE TABLE analytics.executive_kpi_summary AS
WITH totals AS (
    SELECT
        ROUND(SUM(revenue), 2)  AS lifetime_revenue,
        ROUND(SUM(margin), 2)   AS lifetime_margin,
        SUM(order_count)        AS lifetime_orders
    FROM analytics.monthly_revenue
),
latest_complete_month_key AS (
    -- The most recent calendar month in the data (2025-06) is a partial
    -- month — the source extract simply stops mid-June, not a real demand
    -- drop — so it would make MoM growth look like a ~98% crash. This picks
    -- the latest COMPLETE month generically, as the most recent month whose
    -- order_count is at least 50% of its trailing 3-month average, so the
    -- logic still works if this pipeline is re-run later against a fuller
    -- extract.
    SELECT mr3.month_start
    FROM analytics.monthly_revenue mr3
    WHERE mr3.order_count >= 0.5 * (
        SELECT AVG(order_count) FROM analytics.monthly_revenue mr4
        WHERE mr4.month_start BETWEEN mr3.month_start - INTERVAL '3 months' AND mr3.month_start - INTERVAL '1 month'
    ) OR mr3.month_start = (SELECT MIN(month_start) FROM analytics.monthly_revenue WHERE order_count > 0)
    ORDER BY mr3.month_start DESC LIMIT 1
),
latest_month AS (
    SELECT mr.* FROM analytics.monthly_revenue mr, latest_complete_month_key k
    WHERE mr.month_start = k.month_start
),
partial_month AS (
    -- The trailing partial month, reported separately (as a single row,
    -- NULL if there is none) so it is never silently dropped from the
    -- dataset, only excluded from the growth headline.
    SELECT
        (SELECT mr.year_month_label FROM analytics.monthly_revenue mr
         WHERE mr.month_start = (SELECT MAX(month_start) FROM analytics.monthly_revenue)
           AND mr.month_start <> (SELECT month_start FROM latest_complete_month_key)) AS year_month_label,
        (SELECT mr.revenue FROM analytics.monthly_revenue mr
         WHERE mr.month_start = (SELECT MAX(month_start) FROM analytics.monthly_revenue)
           AND mr.month_start <> (SELECT month_start FROM latest_complete_month_key)) AS revenue
),
top_territory AS (
    SELECT territory_name, total_revenue FROM analytics.regional_performance ORDER BY total_revenue DESC LIMIT 1
),
bottom_territory AS (
    SELECT territory_name, total_revenue FROM analytics.regional_performance ORDER BY total_revenue ASC LIMIT 1
),
top_product AS (
    SELECT product_name, total_revenue FROM analytics.product_performance_ranking ORDER BY total_revenue DESC LIMIT 1
),
top_segment AS (
    SELECT customer_segment, segment_revenue FROM analytics.customer_retention_summary ORDER BY segment_revenue DESC LIMIT 1
),
top_salesperson AS (
    SELECT salesperson_name, total_revenue FROM analytics.salesperson_rankings ORDER BY total_revenue DESC LIMIT 1
),
inventory_risk AS (
    SELECT
        SUM(CASE WHEN stock_status IN ('Out of Stock','Low Stock (At/Below Reorder Point)') THEN 1 ELSE 0 END) AS at_risk_products,
        SUM(CASE WHEN stock_status = 'Overstocked' THEN 1 ELSE 0 END) AS overstocked_products
    FROM analytics.inventory_health
),
repeat_rate AS (
    SELECT ROUND(100.0 * SUM(CASE WHEN is_repeat_customer THEN 1 ELSE 0 END) / COUNT(*), 2) AS repeat_customer_rate_pct
    FROM analytics.customer_segments
)
SELECT
    NOW()::date                                   AS report_generated_date,
    t.lifetime_revenue,
    t.lifetime_margin,
    ROUND(t.lifetime_margin / NULLIF(t.lifetime_revenue,0) * 100, 2) AS lifetime_margin_pct,
    t.lifetime_orders,
    lm.year_month_label                           AS latest_complete_month,
    lm.revenue                                    AS latest_complete_month_revenue,
    lm.mom_growth_pct                             AS latest_complete_month_mom_growth_pct,
    pm.year_month_label                           AS partial_month_in_data,
    pm.revenue                                    AS partial_month_revenue_so_far,
    tt.territory_name                             AS top_territory,
    tt.total_revenue                              AS top_territory_revenue,
    bt.territory_name                             AS lowest_territory,
    bt.total_revenue                              AS lowest_territory_revenue,
    tp.product_name                               AS top_product,
    tp.total_revenue                              AS top_product_revenue,
    ts.customer_segment                           AS top_customer_segment,
    ts.segment_revenue                            AS top_segment_revenue,
    tsp.salesperson_name                          AS top_salesperson,
    tsp.total_revenue                             AS top_salesperson_revenue,
    rr.repeat_customer_rate_pct,
    ir.at_risk_products,
    ir.overstocked_products
FROM totals t, latest_month lm, partial_month pm, top_territory tt, bottom_territory bt,
     top_product tp, top_segment ts, top_salesperson tsp, inventory_risk ir, repeat_rate rr;

-- Final full-pipeline checkpoint: list every analytics table with its row count.
SELECT table_name,
       (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM analytics.%I', table_name), false, true, '')))[1]::text::int AS row_count
FROM information_schema.tables
WHERE table_schema = 'analytics'
ORDER BY table_name;
