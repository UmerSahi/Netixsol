"""Task 4: verified property recommendation engine.

Filtering is performed in SQL for exact constraints; amenity matching is a
small deterministic ranking layer over the verified locality KB.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from config import get_engine, PROJECT_ROOT

engine = get_engine()


def _normalise_locality(value: str) -> str:
    """Convert property locality values to the locality-name key used by KB tables."""
    return str(value).split(",", 1)[0].strip()


def recommend_properties(
    budget_max,
    city,
    bedrooms=None,
    area_marla=None,
    property_type=None,
    purpose="For Sale",
    locality_contains=None,
    desired_amenities=None,
    investment_goal=False,
    top_n=5,
):
    """Return verified listings ranked by requested amenities and price.

    Exact filters remain SQL filters. Amenity ranking is based on the same
    locality KB used by the semantic RAG layer, so recommendations do not
    invent property-level amenities.
    """
    if budget_max is None or float(budget_max) < 0:
        raise ValueError("budget_max must be a non-negative number")
    if top_n <= 0:
        raise ValueError("top_n must be greater than zero")

    query = """
        SELECT property_id, property_type, locality, city, price, area_marla,
               bedrooms, baths, agent_id, agent
        FROM properties
        WHERE city = :city AND purpose = :purpose AND price <= :budget_max
    """
    params = {"city": city, "purpose": purpose, "budget_max": budget_max}
    if bedrooms is not None:
        query += " AND bedrooms >= :bedrooms"
        params["bedrooms"] = bedrooms
    if area_marla is not None:
        query += " AND area_marla = :area_marla"
        params["area_marla"] = area_marla
    if property_type:
        query += " AND property_type = :property_type"
        params["property_type"] = property_type
    if locality_contains:
        query += " AND locality LIKE :locality_pattern"
        params["locality_pattern"] = f"%{locality_contains}%"

    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn, params=params)
        amenities_df = pd.read_sql_query(text("SELECT * FROM amenities"), conn)
        locations_df = pd.read_sql_query(text("SELECT * FROM locations"), conn)

    if df.empty:
        return df, "No properties matched the given filters in the verified knowledge base."

    desired = {str(a).strip().casefold() for a in (desired_amenities or []) if str(a).strip()}
    locality_to_full = {
        str(row.locality_name).strip().casefold(): str(row.locality_full)
        for row in locations_df.itertuples()
    }

    amenity_sets = {}
    for lf, group in amenities_df.groupby("locality_full"):
        amenity_sets[str(lf)] = {str(a).strip().casefold() for a in group["amenity"].dropna()}

    def amenity_score(locality):
        if not desired:
            return 0
        locality_name = _normalise_locality(locality).casefold()
        full = locality_to_full.get(locality_name)
        if not full:
            return 0
        return len(desired.intersection(amenity_sets.get(full, set())))

    df["amenity_match"] = df["locality"].apply(amenity_score)
    df["price_per_marla"] = (df["price"] / df["area_marla"]).round(0)

    if investment_goal:
        df = df.sort_values(
            ["amenity_match", "price_per_marla", "price"],
            ascending=[False, True, True],
        )
    else:
        df = df.sort_values(
            ["amenity_match", "price"],
            ascending=[False, True],
        )

    return df.head(top_n).reset_index(drop=True), "OK"


if __name__ == "__main__":
    result, status = recommend_properties(
        budget_max=25_000_000,
        city="Lahore",
        bedrooms=3,
        purpose="For Sale",
        desired_amenities=["24/7 Security", "Community Park"],
    )
    print(status)
    print(result.to_string(index=False))
