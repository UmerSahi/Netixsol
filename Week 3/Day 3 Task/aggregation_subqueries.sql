--Find the total revenue generated per store

SELECT s.store_id, SUM(p.amount) AS total_revenue
FROM payment p
JOIN staff st ON p.staff_id = st.staff_id
JOIN store s ON st.store_id = s.store_id
GROUP BY s.store_id
ORDER BY total_revenue DESC;

--Find the average rental duration per film category

SELECT c.name AS category_name, AVG(f.rental_duration) AS average_rental_duration FROM film f
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id
GROUP BY c.name,c.category_id ORDER BY average_rental_duration DESC;

--OR WE CAN ALSO USE 
SELECT
    c.name AS category_name,
    ROUND(AVG(EXTRACT(EPOCH FROM (r.return_date - r.rental_date)) / 86400), 2) AS avg_rental_days
FROM rental r
JOIN inventory i ON r.inventory_id = i.inventory_id
JOIN film f ON i.film_id = f.film_id
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id
WHERE r.return_date IS NOT NULL
GROUP BY c.name
ORDER BY avg_rental_days DESC;

-- Find the number of rentals made each month

SELECT TO_CHAR(rental_date,'Month YYYY') AS rental_month, COUNT(rental_id) AS total_rentals
FROM rental GROUP BY DATE_TRUNC('month',rental_date), TO_CHAR(rental_date,'Month YYYY')
ORDER BY DATE_TRUNC('month',rental_date) DESC;

-- Find categories with more than 50 films (use HAVING)

SELECT c.name AS category_name, COUNT(fc.film_id) AS total_films FROM category c
JOIN film_category fc ON c.category_id = fc.category_id
GROUP BY c.name,c.category_id HAVING COUNT(fc.film_id) > 50 ORDER BY total_films DESC;

-- Find customers who spent more than the average customer spend

SELECT c.customer_id,CONCAT(c.first_name, ' ', c.last_name) AS customer_name,SUM(p.amount) AS total_spent
FROM customer c JOIN payment p ON c.customer_id = p.customer_id
GROUP BY c.customer_id,c.first_name,c.last_name
HAVING SUM(p.amount) > (SELECT AVG(customer_total)
    FROM (
        SELECT
            SUM(amount) AS customer_total
        FROM payment
        GROUP BY customer_id
    ) AS avg_spend
)
ORDER BY total_spent DESC;

--Find the film(s) with the highest rental rate in each category (use a correlated subquery)
-- In this all films with highest rental rate will be shown if one category has more than one 
-- film with same high rental rate then all of the films with highest rental rate will be shown

SELECT c.name AS category_name,f.title AS film_title,f.rental_rate FROM film f
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id
WHERE f.rental_rate = (
    SELECT MAX(f2.rental_rate) FROM film f2 
	JOIN film_category fc2 ON f2.film_id = fc2.film_id
    WHERE fc2.category_id = fc.category_id
)
ORDER BY c.name,f.title;

--Find customers who have never rented a film (use NOT IN / NOT EXISTS)

SELECT c.customer_id,CONCAT(c.first_name, ' ', c.last_name) AS customer_name,c.email
FROM customer c
WHERE NOT EXISTS (
    SELECT 1
    FROM rental r
    WHERE r.customer_id = c.customer_id
); 
-- THERE ARE NO NULL VALUES IN DATA THAT'S WHY THERE IS NO VALUE IN OUTPUT

-- Find the store with the highest total revenue using a subquery in the WHERE clause

SELECT s.store_id, SUM(p.amount) AS total_revenue FROM payment p
JOIN staff st ON p.staff_id = st.staff_id
JOIN store s ON st.store_id = s.store_id
WHERE s.store_id = (
    SELECT st2.store_id FROM payment p2
    JOIN staff st2 ON p2.staff_id = st2.staff_id
    GROUP BY st2.store_id
    ORDER BY SUM(p2.amount) DESC
    LIMIT 1
)
GROUP BY s.store_id;

--Using a CTE, rank customers by total spend within each city.

WITH customer_spend AS (
    SELECT c.customer_id,CONCAT(c.first_name, ' ', c.last_name) AS customer_name,ci.city AS city_name,
    SUM(p.amount) AS total_spent
    FROM customer c
    JOIN address a ON c.address_id = a.address_id
    JOIN city ci ON a.city_id = ci.city_id
    JOIN payment p ON c.customer_id = p.customer_id
    GROUP BY c.customer_id, c.first_name, c.last_name, ci.city
)
SELECT city_name,customer_name,total_spent,
RANK() OVER (PARTITION BY city_name ORDER BY total_spent DESC) AS city_rank
FROM customer_spend
ORDER BY city_name, city_rank;

--Using ROW_NUMBER(), find the most recently rented film for each customer

WITH ranked_rentals AS (
    SELECT r.customer_id,CONCAT(c.first_name, ' ', c.last_name) AS customer_name,f.title AS film_title,
        r.rental_date,
        ROW_NUMBER() OVER (
            PARTITION BY r.customer_id
            ORDER BY r.rental_date DESC
        ) AS rn
    FROM rental r
    JOIN customer c ON r.customer_id = c.customer_id
    JOIN inventory i ON r.inventory_id = i.inventory_id
    JOIN film f ON i.film_id = f.film_id
)
SELECT customer_id, customer_name, film_title, rental_date
FROM ranked_rentals WHERE rn = 1 ORDER BY customer_name;

--Using a CTE, calculate month-over-month rental revenue growth

WITH monthly_revenue AS (
    SELECT DATE_TRUNC('month', payment_date) AS revenue_month,SUM(amount) AS total_revenue
    FROM payment
    GROUP BY DATE_TRUNC('month', payment_date)
),
revenue_growth AS (
    SELECT revenue_month,total_revenue,
        LAG(total_revenue) OVER (ORDER BY revenue_month) AS prev_month_revenue
    FROM monthly_revenue
)
SELECT TO_CHAR(revenue_month, 'month-YYYY') AS month,total_revenue,prev_month_revenue,
    ROUND(
        (total_revenue - prev_month_revenue) / prev_month_revenue * 100,
        2
    ) AS growth_percent
FROM revenue_growth ORDER BY revenue_month;

--Find the top 3 highest-grossing films per category using RANK() inside a CTE

WITH film_revenue AS (
    SELECT c.name AS category_name,f.title AS film_title,SUM(p.amount) AS total_revenue FROM payment p
    JOIN rental r ON p.rental_id = r.rental_id
    JOIN inventory i ON r.inventory_id = i.inventory_id
    JOIN film f ON i.film_id = f.film_id
    JOIN film_category fc ON f.film_id = fc.film_id
    JOIN category c ON fc.category_id = c.category_id
    GROUP BY c.name, f.title
),
ranked_films AS (
    SELECT category_name,film_title,total_revenue,
        RANK() OVER (
            PARTITION BY category_name
            ORDER BY total_revenue DESC
        ) AS revenue_rank
    FROM film_revenue
)
SELECT category_name, film_title, total_revenue, revenue_rank
FROM ranked_films
WHERE revenue_rank <= 3
ORDER BY category_name, revenue_rank;

--Which staff member processed the highest revenue in each store, and what percentage of that store's 
--total revenue did they contribute? This requires combining aggregation, a CTE, and a percentage 
--calculation in the same query.

WITH staff_revenue AS (
  SELECT st.staff_id,CONCAT(st.first_name, ' ', st.last_name) AS staff_name,st.store_id,
  SUM(p.amount) AS staff_total FROM payment p
    JOIN staff st ON p.staff_id = st.staff_id
    GROUP BY st.staff_id, st.first_name, st.last_name, st.store_id
),
store_revenue AS (
    SELECT store_id, SUM(staff_total) AS store_total FROM staff_revenue GROUP BY store_id
), 
ranked_staff AS (
    SELECT sr.store_id,sr.staff_name,sr.staff_total,
        RANK() OVER (
            PARTITION BY sr.store_id
            ORDER BY sr.staff_total DESC
        ) AS staff_rank
    FROM staff_revenue sr
)
SELECT rs.store_id,rs.staff_name,rs.staff_total,sto.store_total,
    ROUND(rs.staff_total / sto.store_total * 100, 2) AS pct_of_store_revenue
FROM ranked_staff rs
JOIN store_revenue sto ON rs.store_id = sto.store_id
WHERE rs.staff_rank = 1
ORDER BY rs.store_id;