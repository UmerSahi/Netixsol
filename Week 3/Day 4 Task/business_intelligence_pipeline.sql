/*===============================================================
 Task 1: Build Customer Spending Profiles
===============================================================*/

WITH customer_purchases AS (

    /*-----------------------------------------------------------
      CTE 1: Customer Purchases

      Purpose:
      Join all required tables to create one detailed dataset
      containing every purchased track by every customer.
    -----------------------------------------------------------*/

    SELECT
        c.customer_id,CONCAT(c.first_name, ' ', c.last_name) AS customer_name,c.country,
		i.invoice_id,i.invoice_date,
		il.track_id,il.quantity,il.unit_price,
		t.genre_id,
        g.name AS genre_name,
		ar.artist_id,ar.name AS artist_name
    FROM customer c
        JOIN invoice i ON c.customer_id = i.customer_id
        JOIN invoice_line il ON i.invoice_id = il.invoice_id
        JOIN track t ON il.track_id = t.track_id
        JOIN genre g ON t.genre_id = g.genre_id
        JOIN album al ON t.album_id = al.album_id
        JOIN artist ar ON al.artist_id = ar.artist_id
),

customer_profile AS (

    /*-----------------------------------------------------------
      CTE 2: Customer Spending Profile

      Purpose:
      Aggregate purchase information customer wise.
    -----------------------------------------------------------*/

    SELECT customer_id,customer_name,country,
        /* Total money spent by customer */
        SUM(unit_price * quantity) AS total_amount_spent,
        /* Number of invoices */
        COUNT(DISTINCT invoice_id) AS total_invoices,
        /* Total tracks purchased */
        SUM(quantity) AS total_tracks_purchased,
        /* Number of different genres purchased */
        COUNT(DISTINCT genre_id) AS unique_genres_purchased,
        /* Number of different artists purchased */
        COUNT(DISTINCT artist_id) AS unique_artists_purchased,
        /* Number of months customer purchased */
        COUNT(
            DISTINCT DATE_TRUNC('month', invoice_date)
        ) AS purchase_months,
        /* Average amount spent per invoice */
        ROUND(
            SUM(unit_price * quantity) /
            COUNT(DISTINCT invoice_id),
            2
        ) AS average_invoice_value
    FROM customer_purchases
    GROUP BY customer_id,customer_name,country
),

/*===============================================================
 Task 2: Customer Segmentation
 ---------------------------------------------------------------
 Objective:
 Classify customers into Platinum, Gold, Silver, and Bronze
 using multiple business metrics.

 Metrics Used:
 - Total Amount Spent
 - Total Invoices
 - Genre Diversity
===============================================================*/

customer_segments AS (

    SELECT *,
        CASE
            /* Highest-value customers */
            WHEN total_amount_spent >= 40
             AND total_invoices >= 7
             AND unique_genres_purchased >= 5
            THEN 'Platinum'
            /* Loyal customers */
            WHEN total_amount_spent >= 25
             AND total_invoices >= 5
             AND unique_genres_purchased >= 3
            THEN 'Gold'
            /* Moderate customers */
            WHEN total_amount_spent >= 10
             AND total_invoices >= 2
            THEN 'Silver'
            /* New or inactive customers */
            ELSE 'Bronze'
        END AS customer_segment
    FROM customer_profile
),
/*===============================================================
 Task 3: Personalized Marketing Recommendations
---------------------------------------------------------------
 Objective:
 1. Determine each customer's favorite genre.
 2. Recommend a marketing campaign based on customer segment.
 3. Use window functions to identify the favorite genre.
===============================================================*/

-- Count how many tracks each customer bought, broken down by genre.
    -- Grouping by (customer_id, genre_name) collapses all purchase rows
    -- into one row per customer-genre combo, summing quantities across
    -- every track/invoice that falls under that genre.
customer_genre_counts AS (
    SELECT cp.customer_id,cp.genre_name,SUM(cp.quantity) AS total_tracks
    FROM customer_purchases cp
    GROUP BY cp.customer_id,cp.genre_name
),
-- Rank each customer's genres by how many tracks they bought in that genre.
    -- ROW_NUMBER() resets for every customer (PARTITION BY customer_id)
    -- and orders genres from most-purchased to least (ORDER BY total_tracks DESC).
    -- genre_rank = 1 will be that customer's favorite (most-purchased) genre.
favorite_genres AS (
    SELECT customer_id,genre_name,total_tracks,
        ROW_NUMBER() OVER (
            PARTITION BY customer_id
            ORDER BY total_tracks DESC
        ) AS genre_rank
    FROM customer_genre_counts
),

customer_favorite_genre AS (
    SELECT customer_id,genre_name AS favorite_genre FROM favorite_genres WHERE genre_rank = 1
),
-- Assign a targeted marketing campaign to each customer based on
    -- their spending segment (Platinum/Gold/Silver/other), personalizing
    -- the Silver-tier offer with the customer's actual favorite genre.
marketing_recommendations AS (
    SELECT cs.customer_id,cs.customer_name,cs.customer_segment,cfg.favorite_genre,
        CASE
            WHEN cs.customer_segment = 'Platinum'
                THEN 'Early access to new releases'
            WHEN cs.customer_segment = 'Gold'
                THEN 'Album bundle offers'
            WHEN cs.customer_segment = 'Silver'
                THEN CONCAT('20% discount on ', cfg.favorite_genre, ' tracks')
            ELSE 'First purchase coupon'
        END AS marketing_campaign
    FROM customer_segments cs
    JOIN customer_favorite_genre cfg ON cs.customer_id = cfg.customer_id
),
/*===============================================================
 Task 4: Country Expansion Strategy
===============================================================*/
 -- Aggregate customer_profile + customer_purchases up to country level:
    -- revenue, customer count, averages, and purchase diversity per country.
country_metrics AS (
 -- sum of each customer's total spend, by country
    SELECT cp.country,SUM(cp.total_amount_spent) AS total_revenue,
	  -- how many distinct customers per country
        COUNT(DISTINCT cp.customer_id) AS total_customers,
		-- average spend per customer
        ROUND(AVG(cp.total_amount_spent),2) AS avg_revenue_per_customer,
		-- average of customers' average invoice values
        ROUND(AVG(cp.average_invoice_value),2) AS avg_invoice_value,
		 -- how many distinct genres bought in this country
        COUNT(DISTINCT p.genre_id) AS genres_purchased,
		-- how many distinct artists bought in this country
        COUNT(DISTINCT p.artist_id) AS customer_diversity
    FROM customer_profile cp
    JOIN customer_purchases p ON cp.customer_id = p.customer_id GROUP BY cp.country
),
-- Build a composite "country score" (0–1 scale) by normalizing each
    -- metric against its own max value across all countries (min-max
    -- style scaling using the highest country as the 1.0 benchmark),
    -- then combining the normalized metrics using weighted importance.
    -- Weights sum to 1.00 (0.40 + 0.20 + 0.15 + 0.15 + 0.05 + 0.05),
    -- so country_score itself lands between 0 and 1.
country_scores AS (
    SELECT *,
        ROUND(
            (
			 -- Revenue is weighted heaviest (40%) — the primary business driver
                (total_revenue /
                    MAX(total_revenue) OVER()) * 0.40
                +
				-- Revenue per customer (20%) — rewards high-value customer bases, not just volume
                (avg_revenue_per_customer /
                    MAX(avg_revenue_per_customer) OVER()) * 0.20
                +
				-- Average invoice value (15%) — rewards countries with larger basket sizes
                (avg_invoice_value /
                    MAX(avg_invoice_value) OVER()) * 0.15
                +
				-- Total customers (15%) — rewards market size/reach
                (total_customers::NUMERIC /
                    MAX(total_customers) OVER()) * 0.15
                +
				-- Genre diversity (5%) — minor bonus for broad catalog engagement
                (genres_purchased::NUMERIC /
                    MAX(genres_purchased) OVER()) * 0.05
                +
				 -- Artist diversity (5%) — minor bonus for broad artist engagement
                (customer_diversity::NUMERIC /
                    MAX(customer_diversity) OVER()) * 0.05
            ),
            4
        ) AS country_score
    FROM country_metrics
),
 -- Rank countries by their composite score, highest first.
    -- RANK() (not ROW_NUMBER()) is used so that countries with an
    -- identical country_score share the same rank, and the next
country_ranking AS (
    SELECT *,
        RANK() OVER(
            ORDER BY country_score DESC
        ) AS country_rank
    FROM country_scores
),
-- Aggregate customer_segments up to the segment level (Platinum/
    -- Gold/Silver/etc): how many customers per segment, their combined
    -- revenue, and their average spend per customer.
-- Revenue by segment
segment_revenue AS (
    SELECT customer_segment,COUNT(*) AS total_customers,SUM(total_amount_spent) AS total_revenue,
        ROUND(AVG(total_amount_spent),2) AS avg_customer_spend
    FROM customer_segments GROUP BY customer_segment
),
 -- Find the single highest-spending customer within each segment.
    -- Inner query ranks customers 1..N inside their own segment
    -- (PARTITION BY customer_segment) by spend, highest first;
    -- outer query keeps only rn = 1, i.e. the top spender per segment.
-- Each Segment top customer
segment_top_customer AS (
    SELECT *
    FROM (
        SELECT customer_segment, customer_name, total_amount_spent,
            ROW_NUMBER() OVER( PARTITION BY customer_segment ORDER BY total_amount_spent DESC) AS rn
        FROM customer_segments
    ) t
    WHERE rn = 1
),
segment_genre_counts AS (
    SELECT cs.customer_segment,cfg.favorite_genre,COUNT(*) AS customers
    FROM customer_segments cs
    JOIN customer_favorite_genre cfg ON cs.customer_id = cfg.customer_id
    GROUP BY cs.customer_segment,cfg.favorite_genre
),
-- For each segment, count how many customers have each favorite genre.
    -- Join brings each customer's favorite_genre into the segment context;
    -- grouping by (customer_segment, favorite_genre) tallies how many
    -- customers in that segment share the same top genre.
top_genre_segment AS (
    SELECT *
    FROM (
        SELECT
            *,
            ROW_NUMBER() OVER(
                PARTITION BY customer_segment
                ORDER BY customers DESC
            ) AS rn
        FROM segment_genre_counts
    ) x
    WHERE rn=1
),
best_country AS (
    SELECT * FROM country_ranking WHERE country_rank=1
),
-- Calculate what share of TOTAL global revenue each country contributes.
    -- SUM(total_revenue) OVER() (no PARTITION BY) sums total_revenue
    -- across ALL rows, giving the grand total as a constant on every row.
    -- Each country's revenue is then expressed as a percentage of that whole.
country_revenue AS (
    SELECT country,total_revenue,
        ROUND(
            total_revenue *100/
            SUM(total_revenue) OVER(),
            2
        ) AS revenue_percentage
    FROM country_metrics
),
 -- Total revenue generated by each employee, via the customers
    -- they support (support_rep_id) and those customers' invoices.
    -- Since this joins straight from invoice (not invoice_line),
    -- there's no line-item duplication — SUM(i.total) is safe here.
employee_revenue AS (
    SELECT
        e.employee_id,
        CONCAT(e.first_name,' ',e.last_name) AS employee_name,
        SUM(i.total) AS revenue_generated
    FROM employee e
    JOIN customer c ON e.employee_id=c.support_rep_id
    JOIN invoice i ON c.customer_id=i.customer_id
    GROUP BY
        e.employee_id,
        employee_name
),
-- Identify the single top-performing employee by revenue generated.
    -- Inner query ranks ALL employees together (no PARTITION BY, so it's
    -- a global ranking) from highest revenue_generated to lowest;
    -- outer query keeps only rn = 1, i.e. the #1 revenue earner.
top_employee AS (
    SELECT *
    FROM(
        SELECT
            *,
            ROW_NUMBER() OVER(
                ORDER BY revenue_generated DESC
            ) rn
        FROM employee_revenue
    ) t
    WHERE rn=1
),
  -- Total revenue generated by each artist, computed at the line-item
    -- grain (quantity * unit_price per purchase), summed across all
    -- customers who bought that artist's tracks.
artist_revenue AS (
    SELECT
        cp.artist_name,
        SUM(cp.quantity*cp.unit_price) AS revenue

    FROM customer_purchases cp

    GROUP BY cp.artist_name

),
-- Identify the single highest-revenue-generating artist overall.
    -- Inner query ranks ALL artists globally (no PARTITION BY) from
    -- highest revenue to lowest; outer query keeps only rn = 1,
    -- i.e. the top-earning artist.
top_artist AS (
    SELECT *
    FROM(
        SELECT
            *,
            ROW_NUMBER() OVER(
                ORDER BY revenue DESC
            ) rn
        FROM artist_revenue
    ) t
    WHERE rn=1
),
 -- Total revenue generated by each album, computed directly from
    -- invoice_line (the true line-item grain) joined through track to
    -- album — independent of customer_purchases, so no reliance on
    -- that CTE's joins or filtering.
album_revenue AS (
    SELECT
        al.title AS album_name,
        SUM(il.quantity * il.unit_price) AS revenue
    FROM invoice_line il
    JOIN track t
        ON il.track_id = t.track_id
    JOIN album al
        ON t.album_id = al.album_id
    GROUP BY al.title
),
top_album AS (
    SELECT *
    FROM (
        SELECT
            *,
            ROW_NUMBER() OVER(
                ORDER BY revenue DESC
            ) rn
        FROM album_revenue
    ) t
    WHERE rn=1
),
 -- Full behavioral summary per segment: customer count, revenue,
    -- and average engagement metrics (spend, invoices, tracks, genres).
    -- Since customer_segments is one row per customer, these aggregates
    -- are computed cleanly with no join-induced duplication.
segment_summary AS (
    SELECT
        customer_segment,
        COUNT(customer_id) AS total_customers,
        ROUND(SUM(total_amount_spent),2) AS total_revenue,
        ROUND(AVG(total_amount_spent),2) AS avg_customer_spend,
        ROUND(AVG(total_invoices),2) AS avg_invoices,
        ROUND(AVG(total_tracks_purchased),2) AS avg_tracks_purchased,
        ROUND(AVG(unique_genres_purchased),2) AS avg_genres_purchased
    FROM customer_segments
    GROUP BY customer_segment
)
-- ============================================================
-- Final Report: Segment Summary enriched with global "best of" stats
-- ============================================================
SELECT ss.customer_segment,ss.total_customers,ss.total_revenue,tc.customer_name AS top_customer,tg.favorite_genre,
    bc.country AS best_country,te.employee_name,ta.artist_name,alb.album_name
FROM segment_summary ss
-- Per-segment lookups: one matching row per segment, so LEFT JOIN
-- preserves every segment even if a top customer/genre is missing.
LEFT JOIN segment_top_customer tc ON ss.customer_segment = tc.customer_segment
LEFT JOIN top_genre_segment tg ON ss.customer_segment = tg.customer_segment
-- Global "best of" lookups: each of these CTEs returns exactly ONE row
-- (the single best country/employee/artist/album overall), so CROSS JOIN
-- just attaches that same one row onto every segment row below —
-- it's not a real cartesian explosion since there's nothing to multiply.
CROSS JOIN best_country bc
CROSS JOIN top_employee te
CROSS JOIN top_artist ta
CROSS JOIN top_album alb
ORDER BY ss.total_revenue DESC;