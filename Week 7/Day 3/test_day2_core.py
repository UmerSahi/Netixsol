"""Offline regression tests for the non-Google parts of Day 2."""
from pathlib import Path
import pandas as pd

from config import PROJECT_ROOT
from rag_pipeline import chunk_text
from recommendation_engine import recommend_properties


def test_chunk_validation_and_overlap():
    text = "0123456789" * 100
    chunks = chunk_text(text, 100, 20)
    assert len(chunks) > 1
    assert chunks[0][-20:] == chunks[1][:20]


def test_knowledge_base_counts():
    props = pd.read_csv(PROJECT_ROOT / "properties.csv")
    agents = pd.read_csv(PROJECT_ROOT / "agents.csv")
    assert len(props) == 750
    assert len(agents) == 8
    assert props["property_id"].is_unique
    assert props["agent_id"].isin(set(agents["agent_id"])).all()


def test_recommendation_uses_locality_amenities():
    result, status = recommend_properties(
        budget_max=25_000_000,
        city="Lahore",
        bedrooms=3,
        purpose="For Sale",
        desired_amenities=["24/7 Security", "Community Park"],
        top_n=5,
    )
    assert status == "OK"
    assert not result.empty
    assert "amenity_match" in result.columns
    assert result["amenity_match"].max() > 0


if __name__ == "__main__":
    test_chunk_validation_and_overlap()
    test_knowledge_base_counts()
    test_recommendation_uses_locality_amenities()
    print("All offline Day-2 core tests passed.")
