"""
Task 4: Property Recommendation Engine, single-agency schema. Uses
config.py's engine (SQLite or Postgres, per .env).
"""
import pandas as pd
from sqlalchemy import text
from config import get_engine

engine = get_engine()


def recommend_properties(budget_max, city, bedrooms=None, purpose="For Sale",
                          locality_contains=None, desired_amenities=None,
                          investment_goal=False, top_n=5):
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
    if locality_contains:
        query += " AND locality LIKE :locality_pattern"
        params["locality_pattern"] = f"%{locality_contains}%"

    with engine.connect() as conn:
        df = pd.read_sql_query(text(query), conn, params=params)
        amenities_df = pd.read_sql_query(text("SELECT * FROM amenities"), conn)
        locations_df = pd.read_sql_query(text("SELECT * FROM locations"), conn)

    if df.empty:
        return df, "No properties matched the given filters in the verified knowledge base."

    def amenity_score(locality):
        row = locations_df[locations_df['locality_full'] == locality]
        if row.empty or not desired_amenities:
            return 0
        lf = row.iloc[0]['locality_full']
        avail = set(amenities_df[amenities_df['locality_full'] == lf]['amenity'])
        return len(avail.intersection(set(desired_amenities)))

    df['amenity_match'] = df['locality'].apply(amenity_score)
    df['price_per_marla'] = (df['price'] / df['area_marla']).round(0)

    if investment_goal:
        df = df.sort_values(['amenity_match', 'price_per_marla'], ascending=[False, True])
    else:
        df = df.sort_values(['amenity_match', 'price'], ascending=[False, True])

    return df.head(top_n), "OK"


if __name__ == "__main__":
    print("=== Buyer: budget 2.5 crore, Lahore, 3+ bedrooms, wants security+park ===")
    res, status = recommend_properties(
        budget_max=25_000_000, city="Lahore", bedrooms=3, purpose="For Sale",
        desired_amenities=["24/7 Security", "Community Park"])
    print(status)
    print(res[['property_id', 'locality', 'price', 'area_marla', 'bedrooms', 'agent', 'amenity_match']].to_string(index=False))
