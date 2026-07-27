Concept Check Questions

1. What problem does SQL solve that CSV files cannot?

CSV files are just flat text, no built in way to enforce data integrity, relate tables to each other, handle concurrent access, or query efficiently at scale.
SQL lets you store structured, related data with rules (types, constraints), query it declaratively, join multiple datasets together, and let many users or programs read and write safely at the same time.
A CSV file also has to load entirely into memory to work with it while a database can handle datasets far larger than available RAM.

2.What is the difference between a database table and a spreadsheet?

SpreadSheet:
A spreadsheet is flexible and visual cells can hold formulas, mixed types, merged ranges, and formatting.
Spreadsheets are built for direct human viewing and editing.
Database Table:
A database table enforces a strict schema, every row has the same columns, each column has a defined data type, and relationships between tables are explicit (via keys) rather than manual lookups.
Database Tables are built for programmatic querying, integrity, and scale.

3. What is a Primary Key?

A column (or combination of columns) that uniquely identifies each row in a table. It cannot be NULL and cannot repeat.
Example: a customer_id column where every customer has one unique ID.

4. What is a Foreign Key?

A column in one table that references the Primary Key of another table, creating a link between them.
Example: an orders table might have a customer_id column that points back to the customers table, so you know which customer placed each order.

5.What is the difference between WHERE and HAVING?

WHERE filters individual rows before any grouping or aggregation happens.
HAVING filters after aggregation, it operates on the results of GROUP BY, such as filtering groups based on a COUNT or SUM.
Example: WHERE cannot filter on SUM(sales) > 1000 because that sum does not exist until grouping happens that's what HAVING is for.

6. What is the difference between ORDER BY and GROUP BY?

ORDER BY sorts the final result set (ascending or descending), row by row.
GROUP BY collapses multiple rows into summary rows based on shared values, usually paired with aggregate functions like SUM or COUNT.
They solve different problems. One is about sequence, the other is about aggregation.

7. What does DISTINCT do?

It removes duplicate rows from the query result, returning only unique values.
Example: SELECT DISTINCT city FROM customers gives you each city once, no matter how many customers live there.

8. When should you use LIMIT?

Use LIMIT when you only want a subset of rows returned. Common uses include:
• Previewing a large table (LIMIT 10) without pulling everything.
• Getting "top N" results (combined with ORDER BY).
• Pagination (with OFFSET).
• Performance: Avoiding pulling millions of rows when you only need a sample.

9. What are aggregate functions?
Functions that take multiple rows and compute a single summary value. Common ones include COUNT, SUM, AVG, MIN, and MAX.
They are typically used with GROUP BY to summarize data per category, e.g., total sales per region.

10. Why do Data Scientists prefer databases over Excel for large datasets?

• Scale: Excel struggles or crashes well before a million rows, databases handle billions.
• Speed: Indexed queries in a database are far faster than scrolling or filtering in a spreadsheet.
• Integrity: Databases enforce types and constraints, preventing silent data corruption (a common Excel problem, like dates auto converting).
• Concurrency: Multiple people or processes can safely read and write at once.
• Reproducibility: SQL queries are scriptable and version controllable, unlike manual spreadsheet clicks critical for reliable, repeatable analysis pipelines.
• Relationships: Databases handle joins across normalized tables cleanly, whereas Excel relies on fragile VLOOKUPs across sheets.