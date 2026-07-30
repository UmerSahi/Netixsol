# Music Store Business Intelligence Pipeline

A single reusable SQL pipeline (`business_intelligence_pipeline.sql`), built entirely with CTEs, that transforms raw transactional data (customers, invoices, tracks, genres, artists, albums, employees) into customer segments, personalized marketing recommendations, and a country expansion ranking.

---

## 1. Segmentation Logic and Justification

### Logic

Customers are classified into four tiers using a **multi-metric threshold model** (`customer_segments` CTE), based on three behavioral signals computed per customer in `customer_profile`:

| Segment | Total Spent | Total Invoices | Unique Genres | Interpretation |
|---|---|---|---|---|
| **Platinum** | ≥ 40 | ≥ 7 | ≥ 5 | Highest-value, highly engaged, broad taste |
| **Gold** | ≥ 25 | ≥ 5 | ≥ 3 | Loyal, repeat buyers with moderate variety |
| **Silver** | ≥ 10 | ≥ 2 | — | Moderate, occasional buyers |
| **Bronze** | below all above | | | New or inactive customers |

This is implemented as a `CASE` expression evaluated top-down, so a customer must clear **all** thresholds for a tier (spend AND invoices AND, for Platinum/Gold, genre diversity) to qualify — falling short of any one condition drops them to the next tier down.

### Justification

A single metric like total spend is a weak signal on its own — a customer could hit a high spend total from one large one-off purchase, which doesn't reflect real loyalty. The model instead borrows from **RFM-style segmentation** (Recency/Frequency/Monetary), adapted here as:

- **Monetary** → `total_amount_spent` — how much value the customer generates.
- **Frequency** → `total_invoices` — how often they come back, which is a stronger loyalty signal than spend alone.
- **Diversity/Engagement** → `unique_genres_purchased` — used only for the top two tiers, since genre-broad customers are harder to acquire and more resistant to churn (they're not just buying from one favorite artist).

Genre diversity is deliberately excluded from the Silver/Bronze cutoff, since low-frequency customers haven't had enough opportunity to demonstrate variety — it isn't a fair filter at that engagement level.

---

## 2. Country Ranking Methodology

### Logic

Countries are scored and ranked (`country_metrics` → `country_scores` → `country_ranking`) using a **weighted, normalized composite score** rather than raw revenue alone, so that market *quality* (not just market *size*) is reflected.

**Step 1 — Aggregate six metrics per country** (`country_metrics`):
- `total_revenue` — combined spend
- `total_customers` — market reach
- `avg_revenue_per_customer` — customer value density
- `avg_invoice_value` — basket size
- `genres_purchased` — catalog engagement breadth
- `customer_diversity` (distinct artists purchased) — artist engagement breadth

**Step 2 — Normalize each metric to a 0–1 scale** (`country_scores`):
Each metric is divided by its own maximum across all countries using `MAX(metric) OVER()` (a window function with no `PARTITION BY`, so it computes one global max shared by every row). This is min-max-style normalization anchored at the best-performing country = 1.0, which puts metrics with very different units (dollars, counts) on a comparable scale.

**Step 3 — Apply business-weighted importance:**

| Metric | Weight | Rationale |
|---|---|---|
| Total revenue | 40% | Primary business driver — the bottom line |
| Avg revenue per customer | 20% | Rewards customer *quality*, not just volume |
| Avg invoice value | 15% | Rewards larger basket sizes (upsell potential) |
| Total customers | 15% | Rewards market reach/size |
| Genre diversity | 5% | Minor signal of catalog engagement |
| Artist diversity | 5% | Minor signal of artist engagement |

Weights sum to 1.00, so the final `country_score` lands between 0 and 1.

**Step 4 — Rank** (`country_ranking`): `RANK()` (not `ROW_NUMBER()`) is used so that countries with an identical composite score share the same rank, rather than one being arbitrarily favored.

### Justification for weighting

Revenue and revenue-per-customer together make up 60% of the score deliberately — expansion decisions should prioritize markets that are both large *and* efficient, rather than a market that only looks good because of one large country. Diversity metrics are included but kept low-weight (5% each) since they're secondary signals of engagement depth, not direct revenue drivers.

---

## 3. Marketing Recommendation Strategy

### Logic

Each customer's **favorite genre** is first computed (`customer_genre_counts` → `favorite_genres` → `customer_favorite_genre`) by summing track quantities per customer per genre, then using `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY total_tracks DESC)` to rank each customer's own genres and keep only their #1.

That favorite genre is then joined against `customer_segments` to assign a **segment-specific campaign** (`marketing_recommendations`):

| Segment | Campaign | Strategy |
|---|---|---|
| Platinum | Early access to new releases | Reward loyalty with exclusivity, not discounts — this segment doesn't need a price incentive to buy |
| Gold | Album bundle offers | Encourage larger basket sizes from already-engaged buyers |
| Silver | 20% discount on `{favorite_genre}` tracks | Personalized price incentive, targeted at their proven interest, to push them toward Gold |
| Bronze | First purchase coupon | Low-cost acquisition/re-activation nudge for inactive or new customers |

### Justification

The strategy follows a principle of **matching incentive type to loyalty level**: high-value customers respond better to status/access rewards (which protect margin), while lower-engagement customers need direct price incentives to justify a first or next purchase. Personalizing the Silver-tier discount by genre (rather than a generic "20% off everything") increases relevance and conversion likelihood without needing new customer research — the data to personalize it is already computed earlier in the same pipeline.

---

## 4. Actionable Recommendations

1. **Prioritize expansion budget toward the top-ranked country** (`best_country`, `country_rank = 1`) rather than spreading marketing spend evenly — since it scores highest on the weighted blend of revenue, customer value, and engagement, it likely has the best return per dollar invested.

2. **Launch the Silver-tier genre-discount campaign first**, since it's both the largest actionable segment (typically) and the one most likely to move up a tier — a 20% discount targeted at proven taste is a low-risk, high-conversion offer compared to a blanket promotion.

3. **Protect Platinum customers with retention perks, not discounts.** Since they already spend heavily and diversely, discounting to them wastes margin on demand that would have converted anyway — early access and exclusivity preserve full-price revenue while still reinforcing loyalty.

4. **Feature the top artist and top album** (`top_artist`, `top_album`) prominently in homepage/storefront placement and in Gold-tier bundle offers — they've already proven demand at scale, so leading marketing creative with them is a low-risk way to lift average order value.

5. **Recognize and study the top-performing employee's** (`top_employee`) customer support approach, and consider using their account-handling pattern as a coaching template for reps supporting Bronze/Silver customers, where the goal is conversion and retention rather than just service.

6. **Use `country_revenue.revenue_percentage`** (each country's share of global revenue) to set realistic per-market growth targets — a country contributing 2% of revenue shouldn't be judged by the same absolute growth target as one contributing 25%.

7. **Investigate the genre(s) most associated with each segment** (`top_genre_segment`) to inform content licensing and curation priorities — if Platinum customers cluster around a specific genre, prioritizing new releases in that genre protects the most valuable segment's engagement.

---

## 5. Challenges Faced and How They Were Solved

### Challenge 1 — Double-counting revenue across joins
Joining `invoice` (invoice-grain) directly to `invoice_line` (line-item grain) and then summing `invoice.total` would multiply each invoice's total by however many line items it has, inflating spend for anyone who bought more than one track per invoice.
**Solution:** Revenue is computed as `SUM(unit_price * quantity)` directly from the line-item grain (`customer_purchases`), never from `invoice.total` after a line-item join — this keeps the sum accurate regardless of how many tracks are on an invoice.

### Challenge 2 — Mixing grains when aggregating up to country level
`country_metrics` needed both invoice-grain metrics (revenue, invoice count) and line-item-grain metrics (genre/artist diversity). Naively joining `customer_profile` (one row per customer) to `customer_purchases` (one row per line item) and summing `total_amount_spent` directly would have re-summed the same customer total once per purchased track, wildly overstating country revenue.
**Solution:** Metrics were separated by grain — invoice/customer-level aggregates come from `customer_profile` alone, and diversity counts use `COUNT(DISTINCT ...)`, which is immune to row duplication from the join fan-out.

### Challenge 3 — Comparing metrics on different scales for the country score
Revenue (hundreds/thousands of dollars), customer counts (tens), and averages (single/double digits) can't be combined directly — revenue would dominate any weighted sum purely due to its scale.
**Solution:** Each metric is normalized against its own maximum using `MAX(metric) OVER()`, converting every metric to a comparable 0–1 range before applying business weights.

### Challenge 4 — Selecting a single "top" row per group without dropping ties silently
Several CTEs (`top_employee`, `top_artist`, `top_album`, `segment_top_customer`) needed the single highest-value row per group.
**Solution:** `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY metric DESC)` filtered to `rn = 1` was used consistently. This was a deliberate choice over `RANK()` for these cases, since the final report needs exactly one row per lookup to safely support later `CROSS JOIN`s — `RANK()` could return multiple tied rows and break that assumption. (Note: ties are still possible in theory; if a future data update produces a genuine tie, revisit whether `RANK()` + reporting multiple winners is more appropriate for that specific metric.)

### Challenge 5 — Attaching single global "best of" values to every segment row
The final report needed to show one best country, one top employee, one top artist, and one top album alongside *every* segment row, without an explicit join key connecting them to segments.
**Solution:** Since each of those lookup CTEs is guaranteed to return exactly one row (enforced by Challenge 4's approach), a `CROSS JOIN` was used deliberately — with no shared key needed, it simply attaches that one row onto every segment row, which is safe specifically because there's nothing to multiply.
