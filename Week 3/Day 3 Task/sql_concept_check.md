# SQL Concept Check

## 1. WHERE vs HAVING
`WHERE` filters individual rows **before** grouping/aggregation happens. `HAVING` filters groups **after** aggregation (used with `GROUP BY`).

```sql
SELECT department, AVG(salary)
FROM employees
WHERE status = 'active'        -- filters rows first
GROUP BY department
HAVING AVG(salary) > 50000;    -- filters groups after aggregation
```

You can't use an aggregate function like `AVG()` in `WHERE` — that's exactly what `HAVING` is for.

## 2. Correlated Subquery vs JOIN
Use a correlated subquery when you need a **per-row calculation that references the outer query**, especially for existence checks or "top-N per group" logic, and a plain join would either be awkward or produce duplicate rows.

```sql
SELECT e.name, e.salary
FROM employees e
WHERE e.salary > (
    SELECT AVG(e2.salary)
    FROM employees e2
    WHERE e2.department = e.department  -- correlated: references outer e
);
```

A JOIN would require a separate grouped subquery here anyway; the correlated version is often more intuitive for "compare this row to an aggregate of its own group." That said, correlated subqueries run once per outer row, so for large datasets a JOIN with a pre-aggregated subquery is usually faster.

## 3. CTE (Common Table Expression)
A CTE is a temporary named result set defined with `WITH`, scoped to a single query.

```sql
WITH dept_avg AS (
    SELECT department, AVG(salary) AS avg_salary
    FROM employees
    GROUP BY department
)
SELECT e.name, e.salary, d.avg_salary
FROM employees e
JOIN dept_avg d ON e.department = d.department;
```

It's more readable than a nested subquery because:
- It's named and defined **once**, top-down, like a variable
- You avoid deeply indented, nested `SELECT (SELECT (SELECT ...))` blocks
- Multiple CTEs can reference each other, building logic in readable steps
- It can be reused multiple times in the same query without repeating the subquery text

## 4. RANK() vs DENSE_RANK()
Both assign a rank based on `ORDER BY`, but they handle ties differently:

- **`RANK()`**: leaves gaps after ties. If two rows tie for rank 1, the next rank is 3.
- **`DENSE_RANK()`**: no gaps. If two rows tie for rank 1, the next rank is 2.

```
Value   RANK()   DENSE_RANK()
100     1        1
100     1        1
90      3        2
80      4        3
```

## 5. PARTITION BY vs GROUP BY
`GROUP BY` **collapses** rows into one row per group — you lose the individual row detail.

`PARTITION BY` (used with window functions) **keeps every row** but computes the aggregate/window function within each partition, attaching the result to each row.

```sql
SELECT name, department, salary,
       AVG(salary) OVER (PARTITION BY department) AS dept_avg
FROM employees;
```

This returns one row per employee, each showing their department's average alongside their own salary — something `GROUP BY` alone can't do.

## 6. Can a subquery return multiple rows?
Yes. If it does, you can't use `=`, `<`, `>` etc. directly — use:
- **`IN`** — check membership in a list of values
- **`ANY` / `SOME`** — compare against any value in the set
- **`ALL`** — compare against all values in the set
- **`EXISTS`** — check whether the subquery returns any rows at all

```sql
SELECT name
FROM employees
WHERE department_id IN (
    SELECT department_id FROM departments WHERE location = 'NYC'
);
```

## 7. CASE WHEN inside an Aggregate Function
Useful for **conditional aggregation** — turning row-level conditions into separate summary columns without multiple queries.

```sql
SELECT
    department,
    COUNT(CASE WHEN gender = 'F' THEN 1 END) AS female_count,
    COUNT(CASE WHEN gender = 'M' THEN 1 END) AS male_count,
    SUM(CASE WHEN status = 'active' THEN salary ELSE 0 END) AS active_payroll
FROM employees
GROUP BY department;
```

This lets you pivot data into columns (a manual "pivot table") in a single pass over the data, instead of running separate filtered queries for each condition.
