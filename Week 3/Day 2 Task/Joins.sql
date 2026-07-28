-- Query to Display Name,Email,City,Country of Cutomers

SELECT first_name,last_name,email,city,country
FROM customer JOIN address ON customer.address_id = address.address_id
JOIN city ON address.city_id = city.city_id
JOIN country ON city.country_id = country.country_id; 

-- Query to display every payment with customer name,film title and amount paid using order

SELECT CONCAT(first_name,' ',last_name) AS customer_name,
Title AS film_title, amount AS amount_paid FROM payment JOIN customer ON payment.customer_id = customer.customer_id
JOIN rental ON payment.rental_id = rental.rental_id
JOIN inventory ON rental.inventory_id = inventory.inventory_id
JOIN film ON inventory.film_id = film.film_id
ORDER BY customer_name,film_title;

-- Query to display every payment with customer name,film title and amount paid without order

SELECT CONCAT(first_name,' ',last_name) AS customer_name,
Title AS film_title, amount AS amount_paid FROM payment JOIN customer ON payment.customer_id = customer.customer_id
JOIN rental ON payment.rental_id = rental.rental_id
JOIN inventory ON rental.inventory_id = inventory.inventory_id
JOIN film ON inventory.film_id = film.film_id;


-- Query to show top 10 customers based on total_spent

SELECT c.customer_id,CONCAT(c.first_name,' ',c.last_name) AS customer_name,c.email,
SUM(p.amount) AS total_spent FROM customer c
JOIN payment p ON c.customer_id = p.customer_id
GROUP BY c.customer_id,c.first_name,c.last_name,c.email
ORDER BY total_spent DESC LIMIT 10;

-- Query to Display each film with its Category and Rental Rate

SELECT f.title AS film_title, c.name AS category_name, f.rental_rate
FROM film f JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id;

-- Query to  find all actors who appeared in each film

SELECT CONCAT(a.first_name,' ',a.last_name) AS actor_name,f.title AS film_title
FROM actor a JOIN film_actor fa ON a.actor_id = fa.actor_id
JOIN film f ON fa.film_id = f.film_id ORDER BY film_title,actor_name;

-- Count how many films belong to each category

SELECT c.name AS category_name, COUNT(fc.film_id) AS total_films
FROM category c JOIN film_category fc ON c.category_id = fc.category_id 
GROUP BY c.category_id,c.name ORDER BY total_films DESC;

-- Which categories generated the highest revenue

SELECT c.name AS category_name,SUM(p.amount) AS total_revenue
FROM payment p JOIN rental r ON p.rental_id = r.rental_id
JOIN inventory i ON r.inventory_id = i.inventory_id
JOIN film f ON i.film_id = f.film_id
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id
GROUP BY c.category_id,c.name ORDER BY total_revenue DESC;

-- Find customers who have rented more than 20 films

SELECT CONCAT(c.first_name,' ',c.last_name) AS customer_name, COUNT(r.rental_id) AS rented_films
FROM customer c JOIN rental r ON c.customer_id = r.customer_id
GROUP BY c.first_name,c.last_name
HAVING COUNT(r.rental_id) > 20 ORDER BY rented_films DESC;

-- Which cities generated the highest rental revenue

SELECT ci.city AS city_name, SUM(p.amount) AS total_rental_revenue
FROM city ci JOIN address a ON ci.city_id = a.city_id
JOIN customer c ON a.address_id = c.address_id
JOIN payment p ON c.customer_id = p.customer_id
GROUP BY ci.city ORDER BY total_rental_revenue DESC;

-- Bonus Challenge which actor generates highest revenue

SELECT CONCAT(a.first_name,' ',a.last_name) AS actor_name, SUM(p.amount) AS total_revenue_generated
FROM actor a JOIN film_actor fa ON a.actor_id = fa.actor_id
JOIN film f ON fa.film_id =  f.film_id
JOIN inventory i  ON f.film_id = i.film_id
JOIN rental r ON i.inventory_id = r.inventory_id
JOIN payment p ON r.rental_id = p.rental_id 
GROUP BY a.first_name,a.last_name ORDER BY total_revenue_generated DESC LIMIT 1;
