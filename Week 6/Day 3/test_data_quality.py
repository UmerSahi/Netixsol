"""
AFL DATA QUALITY REGRESSION TESTS
==================================
"""

from afl_data import STORE


def test_leaderboard_has_no_fake_player():
    """Unidentified records must never appear as players."""

    result = STORE.player_leaders(
        "disposals",
        2023,
        10,
    )

    assert "Name not listed in data" not in result
    assert "nan —" not in result.lower()
    assert "none —" not in result.lower()


def test_2023_disposals_known_leader():
    """Christian Petracca must be a 2023 disposal leader."""

    result = STORE.player_leaders(
        "disposals",
        2023,
        5,
    )

    assert "Christian Petracca" in result
    assert "695" in result


def test_leaderboard_returns_five_players():
    """Top-five requests must return five ranked players."""

    result = STORE.player_leaders(
        "disposals",
        2023,
        5,
    )

    ranked = [
        line
        for line in result.splitlines()
        if line[:1].isdigit()
    ]

    assert len(ranked) == 5
