"""Local preflight checks for the complete Day-2 project.

This script does not call Google. It validates files, database tables,
row counts, configuration, and whether a Chroma collection exists. Run it
before the live embedding/LLM steps.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text

from config import (
    DB_BACKEND,
    GEMINI_EMBEDDING_MODEL,
    GEMINI_LLM_MODEL,
    GOOGLE_API_KEY,
    PROJECT_ROOT,
    SQLITE_PATH,
    CHROMA_DIR,
    COLLECTION_NAME,
    get_engine,
)

REQUIRED_FILES = [
    "Property_with_Feature_Engineering.csv",
    "properties.csv", "agents.csv", "locations.csv", "amenities.csv",
    "schools.csv", "hospitals.csv", "developers.csv", "payment_plans.csv", "faqs.csv",
]
REQUIRED_TABLES = [
    "properties", "agents", "locations", "amenities", "schools", "hospitals",
    "developers", "payment_plans", "faqs",
]


def main():
    failures = []
    print("=== RealEstate Hub Day-2 Health Check ===")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Database backend: {DB_BACKEND}")
    print(f"Embedding model: {GEMINI_EMBEDDING_MODEL}")
    print(f"LLM model: {GEMINI_LLM_MODEL}")
    print(f"Google API key configured: {'yes' if GOOGLE_API_KEY and 'your_google_api_key_here' not in GOOGLE_API_KEY else 'no'}")

    for name in REQUIRED_FILES:
        path = PROJECT_ROOT / name
        ok = path.exists()
        print(f"FILE  {'OK ' if ok else 'FAIL'} {name}")
        if not ok:
            failures.append(f"Missing {name}")

    try:
        engine = get_engine()
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        for table in REQUIRED_TABLES:
            ok = table in tables
            print(f"DB    {'OK ' if ok else 'FAIL'} {table}")
            if not ok:
                failures.append(f"Missing DB table {table}")
        if not failures:
            with engine.connect() as conn:
                for table in REQUIRED_TABLES:
                    count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
                    print(f"      {table}: {count:,} rows")
    except Exception as exc:
        failures.append(f"Database connection failed: {exc}")
        print(f"DB    FAIL {exc}")

    chroma_path = Path(CHROMA_DIR)
    chroma_ok = chroma_path.exists() and any(chroma_path.iterdir())
    print(f"CHROMA {'OK ' if chroma_ok else 'WARN'} {chroma_path} (collection={COLLECTION_NAME})")
    if not chroma_ok:
        print("      Build it with: python rebuild_vectorstore.py")

    if failures:
        print("\nHealth check FAILED:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print("\nHealth check PASSED. Local project structure is ready.")
    print("Next live step: configure .env with GOOGLE_API_KEY and run rebuild_vectorstore.py.")


if __name__ == "__main__":
    main()
