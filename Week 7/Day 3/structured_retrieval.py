"""
Task 3: Structured (SQL) vs. Semantic (vector) retrieval, single-agency
schema. Uses config.py's engine, so it runs against SQLite or Postgres
depending on DB_BACKEND in .env -- no code change needed either way.
"""
from sqlalchemy import text
from config import get_engine
from rag_pipeline import get_vectorstore, get_retriever, rag_retrieve

engine = get_engine()


def sql_query(sql, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        cols = list(result.keys())
        rows = result.fetchall()
    return cols, rows


if __name__ == "__main__":
    print("=== SQL: exact price lookup ===")
    cols, rows = sql_query(
        "SELECT property_id, locality, price, purpose FROM properties "
        "WHERE city=:city AND purpose=:purpose ORDER BY price ASC LIMIT 5",
        {"city": "Islamabad", "purpose": "For Sale"})
    print(cols); [print(r) for r in rows]

    print("\n=== SQL: availability / plot size filter ===")
    cols, rows = sql_query(
        "SELECT property_id, locality, area_marla, bedrooms, price FROM properties "
        "WHERE city=:city AND area_marla BETWEEN :lo AND :hi AND purpose='For Sale' LIMIT 5",
        {"city": "Lahore", "lo": 8, "hi": 12})
    print(cols); [print(r) for r in rows]

    print("\n=== SQL: agent name lookup (single-agency in-house roster) ===")
    cols, rows = sql_query(
        "SELECT property_id, agent_id, agent FROM properties WHERE city=:city LIMIT 5",
        {"city": "Rawalpindi"})
    print(cols); [print(r) for r in rows]

    print("\n=== SQL: agent workload (listings per RealEstate Hub agent) ===")
    cols, rows = sql_query("SELECT agent_id, agent_name, active_listings FROM agents ORDER BY active_listings DESC")
    print(cols); [print(r) for r in rows]

    print("\n=== VECTOR: semantic FAQ query ===")
    retriever = get_retriever(get_vectorstore(), k=4)
    res = rag_retrieve(retriever, "what documents do I need to buy a house", source_type_filter=["faq"])
    for c in res['chunks']:
        print(round(c['score'], 3), c['text'][:150])
