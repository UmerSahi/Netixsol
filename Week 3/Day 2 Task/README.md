# DVD Rental Database — SQL Practice Queries

This project uses the PostgreSQL **dvdrental** sample database to practice
multi-table `JOIN`s, aggregation, and business-style reporting queries. The
database was restored locally in pgAdmin, and its schema/relationships are
documented below using the ERD generated with pgAdmin's built-in **ERD Tool**.

---

## Relationship Diagram

![DVD Rental ERD](./ERD_Diagram_DVDRENTAL.png)
![alt text](<ERD Diagram DVDRENTAL.png>)

The schema is organized around three connected clusters:

- **Film catalog**: `film` ↔ `film_actor` ↔ `actor`, and `film` ↔ `film_category` ↔ `category`
- **Rental/payment chain**: `film` → `inventory` → `rental` → `payment`, tied to `customer` and `staff`
- **Location data**: `country` → `city` → `address`, referenced by `customer`, `staff`, and `store`

Every table in the diagram connects back to `film` or `customer` eventually,
which is why most reporting queries below have to pass through several
junction tables to connect a name (actor/customer/category) to a dollar amount.

---

## Query-by-Query Explanation

### 1. Customer name, email, city, and country

```sql
SELECT first_name, last_name, email, city, country
FROM customer
JOIN address ON customer.address_id = address.address_id
JOIN city ON address.city_id = city.city_id
JOIN country ON city.country_id = country.country_id;
```

**Joins used:**
- `customer → address` on `address_id` — a customer's row only stores a
  foreign key to their address, not the address text itself.
- `address → city` on `city_id` — the address stores which city it's in.
- `city → country` on `country_id` — the city stores which country it's in.

This is a **chained JOIN**: to go from `customer` to `country`, PostgreSQL
has to walk through two intermediate tables, since there's no direct foreign
key from `customer` straight to `country`. This mirrors the "location cluster"
in the ERD (`country → city → address`).

---

### 2 & 3. Payment details with customer name and film title (ordered / unordered)

```sql
SELECT CONCAT(first_name,' ',last_name) AS customer_name,
       title AS film_title, amount AS amount_paid
FROM payment
JOIN customer ON payment.customer_id = customer.customer_id
JOIN rental ON payment.rental_id = rental.rental_id
JOIN inventory ON rental.inventory_id = inventory.inventory_id
JOIN film ON inventory.film_id = film.film_id
[ORDER BY customer_name, film_title];
```

**Joins used:**
- `payment → customer` on `customer_id` — gets the name of who paid.
- `payment → rental` on `rental_id` — every payment is linked to the rental
  it was for.
- `rental → inventory` on `inventory_id` — a rental doesn't reference a film
  directly, it references a specific **physical copy** in inventory.
- `inventory → film` on `film_id` — the inventory row tells you which film
  that copy is.

This is the longest join chain in the set, and it exists because of how the
schema models "renting a copy of a film" rather than "renting a film" — the
`inventory` table is the bridge between an abstract film title and an actual
rentable item at a specific store. The only difference between query 2 and 3
is the `ORDER BY`; query 3 returns the same rows in whatever order Postgres's
query planner produces them, so results can look different every run.

---

### 4. Top 10 customers by total spend

```sql
SELECT c.customer_id, CONCAT(c.first_name,' ',c.last_name) AS customer_name,
       c.email, SUM(p.amount) AS total_spent
FROM customer c
JOIN payment p ON c.customer_id = p.customer_id
GROUP BY c.customer_id, c.first_name, c.last_name, c.email
ORDER BY total_spent DESC LIMIT 10;
```

**Join used:** `customer → payment` on `customer_id` — a single JOIN is
enough here because `payment` already has a direct foreign key to
`customer_id`. `GROUP BY` then collapses each customer's many payment rows
into one row per customer, and `SUM(amount)` totals them.

---

### 5. Each film with category and rental rate

```sql
SELECT f.title AS film_title, c.name AS category_name, f.rental_rate
FROM film f
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id;
```

**Joins used:**
- `film → film_category` on `film_id` — `film_category` is a **junction
  table** (many-to-many bridge); it exists because a film can have multiple
  categories and a category has many films, which can't be modeled with a
  single foreign key on either side.
- `film_category → category` on `category_id` — resolves the category ID to
  its readable name.

---

### 6. All actors who appeared in each film

```sql
SELECT CONCAT(a.first_name,' ',a.last_name) AS actor_name, f.title AS film_title
FROM actor a
JOIN film_actor fa ON a.actor_id = fa.actor_id
JOIN film f ON fa.film_id = f.film_id
ORDER BY film_title, actor_name;
```

**Joins used:** Same many-to-many pattern as query 5, but for actors:
`actor → film_actor` (junction table) `→ film`. This is necessary because
one actor appears in many films, and one film has many actors.

---

### 7. Film count per category

```sql
SELECT c.name AS category_name, COUNT(fc.film_id) AS total_films
FROM category c
JOIN film_category fc ON c.category_id = fc.category_id
GROUP BY c.category_id, c.name
ORDER BY total_films DESC;
```

**Join used:** `category → film_category` on `category_id`.

---

### 8. Revenue by category

```sql
SELECT c.name AS category_name, SUM(p.amount) AS total_revenue
FROM payment p
JOIN rental r ON p.rental_id = r.rental_id
JOIN inventory i ON r.inventory_id = i.inventory_id
JOIN film f ON i.film_id = f.film_id
JOIN film_category fc ON f.film_id = fc.film_id
JOIN category c ON fc.category_id = c.category_id
GROUP BY c.category_id, c.name
ORDER BY total_revenue DESC;
```

**Joins used:** This chains the "rental/payment cluster" (`payment → rental →
inventory → film`) together with the "catalog cluster" (`film → film_category
→ category`) — five joins total, because revenue lives in `payment` but
category lives four tables away in `category`.

---

### 9. Customers who rented more than 20 films

```sql
SELECT CONCAT(c.first_name,' ',c.last_name) AS customer_name,
       COUNT(r.rental_id) AS rented_films
FROM customer c
JOIN rental r ON c.customer_id = r.customer_id
GROUP BY c.first_name, c.last_name
HAVING COUNT(r.rental_id) > 20
ORDER BY rented_films DESC;
```

**Join used:** `customer → rental` on `customer_id`. `HAVING` (not `WHERE`)
is required here because the filter applies to the *aggregated* count, and
`WHERE` can't reference aggregate functions.

---

### 10. Highest rental revenue by city

```sql
SELECT ci.city AS city_name, SUM(p.amount) AS total_rental_revenue
FROM city ci
JOIN address a ON ci.city_id = a.city_id
JOIN customer c ON a.address_id = c.address_id
JOIN payment p ON c.customer_id = p.customer_id
GROUP BY ci.city
ORDER BY total_rental_revenue DESC;
```

**Joins used:** Walks the location cluster in reverse —
`city → address → customer → payment` — to attach a dollar amount to a
geographic location.

---

### 11. Bonus: actor who generated the most revenue

```sql
SELECT CONCAT(a.first_name,' ',a.last_name) AS actor_name,
       SUM(p.amount) AS total_revenue_generated
FROM actor a
JOIN film_actor fa ON a.actor_id = fa.actor_id
JOIN film f ON fa.film_id = f.film_id
JOIN inventory i ON f.film_id = i.film_id
JOIN rental r ON i.inventory_id = r.inventory_id
JOIN payment p ON r.rental_id = p.rental_id
GROUP BY a.first_name, a.last_name
ORDER BY total_revenue_generated DESC LIMIT 1;
```

**Joins used:** The longest chain in the whole set — six tables — because it
connects the "cast" cluster (`actor → film_actor → film`) all the way through
to the "money" cluster (`film → inventory → rental → payment`). This is a
good example of why understanding the ERD matters: without seeing that `film`
is the hinge point connecting both clusters, this join path isn't obvious.

---

## Three Business Insights


1. **Nearly all active customers are heavy renters** — this isn't a niche segment
   my Query with more than ">20 rentals" query returned 543 of 599 total rows — meaning roughly 91% of all customers rented more than 20 films. Renting frequently isn't a behavior that separates "power users" from casual ones here, it's closer to the baseline. Eleanor Hunt tops the list at 46 rentals, but even the customer at the bottom of a typical cutoff is still well above 20. If you were running this as a real rental business, this tells you retention/frequency programs should probably target the remaining ~9% of low-activity customers rather than trying to "reward" high-frequency renters who are already the norm.

2. **Sci-Fi punches above its weight Foreign underperforms despite a bigger catalog**

   Combining your category-count query and category-revenue query:

   Category	 # Films	Revenue	     Revenue/Film
   Sci-Fi	      61	  $4,336.01	    ~$71.1
   Comedy	      58	  $4,002.48	    ~$69.0
   Drama	      62	  $4,118.46	    ~$66.4
   Sports	      74	  $4,892.19	    ~$66.1
   Foreign	    73	  $3,934.47	    ~$53.9

3. **"top cities by revenue" numbers are identical to your "top customers by spend" numbers**
    Look closely: Saint-Denis ($211.55) matches Eleanor Hunt's total spend exactly; Cape Coral ($208.58) matches Karl Seal's; and so on down the list. That's not a coincidence it means each of these top cities has effectively one paying customer, since the dvdrental sample data spreads customers thinly across ~599 distinct cities.


---

## How to Reproduce

1. Restore `dvdrental` into local PostgreSQL (see setup notes/screenshots).
2. Open pgAdmin → Query Tool on the `dvdrental` database.
3. Run each query from `queries.txt` in order.
4. Cross-reference table relationships using `ERD_Diagram_DVDRENTAL.png`
   (generated via pgAdmin's ERD Tool: right-click database → **ERD Tool**).
