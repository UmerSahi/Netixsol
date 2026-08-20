
"""
AFL DATA-LAYER TESTS
====================

Offline pytest tests for the supplied AFL datasets.

These tests intentionally do not call Gemini or any external API.
They validate that the local CSV data layer loads correctly and that
the main analytics operations return sensible results.

Run with:

    pytest -v test_data_layer.py
"""

import pytest

from afl_data import STORE


def test_player_table_loaded():
    """Verify that the player-game dataset loaded successfully."""
    assert len(STORE.players) > 200_000


def test_match_table_loaded():
    """Verify that the match dataset loaded successfully."""
    assert len(STORE.matches) > 7_000


def test_player_resolution():
    """Verify that a known AFL player can be resolved."""
    result = STORE.resolve_player("Nick Daicos")

    assert result is not None


def test_team_resolution():
    """Verify that a known AFL team can be resolved."""
    result = STORE.resolve_team("Collingwood")

    assert result is not None


def test_player_summary():
    """Verify that player season summaries return useful data."""
    result = STORE.player_summary("Nick Daicos", 2023)

    assert result is not None
    assert "Nick Daicos" in result


def test_team_summary():
    """Verify that team season summaries return useful data."""
    result = STORE.team_summary("Collingwood", 2023)

    assert result is not None
    assert "Collingwood" in result


def test_player_leaderboard():
    """Verify that the player leaderboard returns results."""
    result = STORE.player_leaders("disposals", 2023, 5)

    assert result is not None
    assert "Top" in result


def test_head_to_head():
    """Verify that team head-to-head analysis returns results."""
    result = STORE.head_to_head(
        "Collingwood",
        "Melbourne Demons",
        2023,
        2023,
    )

    assert result is not None
    assert "Head-to-head" in result


def test_match_lookup():
    """Verify that match lookup returns results for two teams."""
    result = STORE.match_lookup(
        "Collingwood",
        2023,
        team_b="Melbourne Demons",
    )

    assert result is not None
    assert "Match results" in result
