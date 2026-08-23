"""
retrieval_tools.py
===================
Section 7: Structured retrieval tools. Every function here queries the
cached CSVs directly with vectorized pandas -- never row-by-row loops, never
LLM memory. Every function returns a plain dict with either a "data" key
(success) or an "error"/"clarification" key (never both silently merged).

SUPPORTED_STATS below is the single source of truth for which per-match
stat names get_top_player_in_match will accept -- this prevents arbitrary,
unsupported stat names being silently accepted.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd
import numpy as np

from data_layer import get_dataset
from resolvers import resolve_team, resolve_player

SUPPORTED_STATS = {
    "disposals": "disposals",
    "kicks": "kicks",
    "handballs": "handballs",
    "marks": "marks",
    "goals": "goals",
    "behinds": "behinds",
    "tackles": "tackles",
    "hit_outs": "hit_outs",
    "hitouts": "hit_outs",
    "clearances": "clearances",
    "inside_50s": "inside_50s",
    "inside 50s": "inside_50s",
    "fantasy_points": "fantasy_points",
    "fantasy points": "fantasy_points",
    "contested_possessions": "contested_possessions",
    "contested possessions": "contested_possessions",
}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _clarify(msg: str, candidates=None) -> dict:
    return {"ok": False, "clarification": msg, "candidates": candidates or []}


def _ok(data) -> dict:
    return {"ok": True, "data": data}


# ---------------------------------------------------------------------------
# 1. get_team_match_in_round
# ---------------------------------------------------------------------------
def get_team_match_in_round(team: str, year: int, round_: str) -> dict:
    """Exact match facts for a team in a given season+round."""
    tr = resolve_team(team)
    if tr.status == "ambiguous":
        return _clarify(tr.message, tr.candidates)
    if tr.status == "not_found":
        return _err(tr.message)
    team_name = tr.value

    mr = get_dataset("match_retrieval")
    round_str = str(round_)
    mask = (mr["season"] == int(year)) & (mr["round"] == round_str) & (
        (mr["home_team"] == team_name) | (mr["away_team"] == team_name)
    )
    rows = mr[mask]
    if rows.empty:
        return _err(
            f"No match found for {team_name} in round {round_str} of the {year} season "
            f"in the dataset."
        )
    row = rows.iloc[0]
    is_home = row["home_team"] == team_name
    opponent = row["away_team"] if is_home else row["home_team"]
    team_score = row["home_score"] if is_home else row["away_score"]
    opp_score = row["away_score"] if is_home else row["home_score"]
    result = "win" if row["winner_team"] == team_name else (
        "draw" if row["result"] == "D" else "loss"
    )
    return _ok({
        "match_id": row["match_id"], "season": int(row["season"]), "round": round_str,
        "date": row["match_date"], "venue": row["venue"],
        "team": team_name, "opponent": opponent, "is_home": bool(is_home),
        "team_score": int(team_score), "opponent_score": int(opp_score),
        "margin": int(team_score - opp_score), "result": result,
        "winner_team": row["winner_team"] if pd.notna(row["winner_team"]) else None,
        "crowd": None if pd.isna(row["crowd"]) else int(row["crowd"]),
    })


# ---------------------------------------------------------------------------
# 2. get_team_head_to_head
# ---------------------------------------------------------------------------
def get_team_head_to_head(team: str, opponent: str) -> dict:
    """All-time head-to-head record between two teams."""
    tr1, tr2 = resolve_team(team), resolve_team(opponent)
    for tr, label in [(tr1, team), (tr2, opponent)]:
        if tr.status == "ambiguous":
            return _clarify(tr.message, tr.candidates)
        if tr.status == "not_found":
            return _err(tr.message)
    t1, t2 = tr1.value, tr2.value
    if t1 == t2:
        return _err("Head-to-head requires two different teams.")

    mr = get_dataset("match_retrieval")
    mask = ((mr["home_team"] == t1) & (mr["away_team"] == t2)) | \
           ((mr["home_team"] == t2) & (mr["away_team"] == t1))
    rows = mr[mask]
    if rows.empty:
        return _err(f"No historical matches found between {t1} and {t2} in the dataset.")

    wins_t1 = (rows["winner_team"] == t1).sum()
    wins_t2 = (rows["winner_team"] == t2).sum()
    draws = (rows["result"] == "D").sum()
    return _ok({
        "team": t1, "opponent": t2,
        "matches_played": int(len(rows)),
        f"{t1}_wins": int(wins_t1), f"{t2}_wins": int(wins_t2), "draws": int(draws),
        "first_meeting": rows["match_date"].min(), "last_meeting": rows["match_date"].max(),
    })


# ---------------------------------------------------------------------------
# 3. get_player_match_stats
# ---------------------------------------------------------------------------
def get_player_match_stats(player: str, year: int, round_: str) -> dict:
    """Exact single-match stat line for a player."""
    pres = resolve_player(player)
    if pres.status == "ambiguous":
        return _clarify(pres.message, pres.candidates)
    if pres.status == "not_found":
        return _err(pres.message)
    name = pres.value

    pr = get_dataset("player_retrieval")
    round_str = str(round_)
    rows = pr[(pr["player_name"] == name) & (pr["year"] == int(year)) & (pr["round"] == round_str)]
    if rows.empty:
        return _err(f"No match record found for {name} in round {round_str} of {year}.")
    row = rows.iloc[0]
    stat_cols = ["kicks", "marks", "handballs", "disposals", "goals", "behinds", "hit_outs",
                 "tackles", "clearances", "inside_50s", "contested_possessions",
                 "uncontested_possessions", "fantasy_points", "player_score"]
    stats = {c: (None if pd.isna(row[c]) else float(row[c])) for c in stat_cols}
    return _ok({
        "player": name, "year": int(year), "round": round_str,
        "team": row["team"], "opponent": row["opponent"], "result": row["result"],
        "match_id": row["match_id"], **stats,
    })


# ---------------------------------------------------------------------------
# 4. get_player_season_stats
# ---------------------------------------------------------------------------
def get_player_season_stats(player: str, year: int) -> dict:
    """Season totals and averages for a player, computed directly from the data."""
    pres = resolve_player(player)
    if pres.status == "ambiguous":
        return _clarify(pres.message, pres.candidates)
    if pres.status == "not_found":
        return _err(pres.message)
    name = pres.value

    pr = get_dataset("player_retrieval")
    rows = pr[(pr["player_name"] == name) & (pr["year"] == int(year))]
    if rows.empty:
        return _err(f"No {year} season data found for {name}.")
    stat_cols = ["disposals", "kicks", "handballs", "marks", "goals", "behinds",
                 "tackles", "hit_outs", "clearances", "inside_50s", "fantasy_points"]
    totals = {f"total_{c}": float(rows[c].sum(skipna=True)) for c in stat_cols}
    averages = {f"avg_{c}": round(float(rows[c].mean(skipna=True)), 2) for c in stat_cols}
    return _ok({
        "player": name, "year": int(year), "games_played": int(len(rows)),
        "team": rows["team"].iloc[-1],
        **totals, **averages,
    })


# ---------------------------------------------------------------------------
# 5. get_top_player_in_match
# ---------------------------------------------------------------------------
def get_top_player_in_match(team: str, year: int, round_: str, stat: str) -> dict:
    """Which player on `team` had the highest value of `stat` in that match."""
    stat_key = stat.strip().lower()
    if stat_key not in SUPPORTED_STATS:
        return _err(
            f"'{stat}' is not a supported statistic. Supported stats: "
            f"{', '.join(sorted(set(SUPPORTED_STATS.values())))}."
        )
    stat_col = SUPPORTED_STATS[stat_key]

    tr = resolve_team(team)
    if tr.status == "ambiguous":
        return _clarify(tr.message, tr.candidates)
    if tr.status == "not_found":
        return _err(tr.message)
    team_name = tr.value

    pr = get_dataset("player_retrieval")
    round_str = str(round_)
    rows = pr[(pr["team"] == team_name) & (pr["year"] == int(year)) & (pr["round"] == round_str)]
    if rows.empty:
        return _err(f"No player data found for {team_name} in round {round_str} of {year}.")
    rows = rows.dropna(subset=[stat_col])
    if rows.empty:
        return _err(f"'{stat_col}' was not recorded for {team_name} in round {round_str} of {year}.")
    top = rows.loc[rows[stat_col].idxmax()]
    return _ok({
        "team": team_name, "year": int(year), "round": round_str, "stat": stat_col,
        "player": top["player_name"], "value": float(top[stat_col]),
        "opponent": top["opponent"],
    })


if __name__ == "__main__":
    print(get_team_match_in_round("Geelong", 2020, 5))
    print(get_team_head_to_head("Cats", "Pies"))
    print(get_player_match_stats("Patrick Dangerfield", 2020, 5))
    print(get_player_season_stats("Patrick Dangerfield", 2020))
    print(get_top_player_in_match("Geelong", 2020, 5, "disposals"))
    print(get_top_player_in_match("Geelong", 2020, 5, "not_a_stat"))
    print(get_team_match_in_round("Giants", 1990, 5))  # GWS didn't exist yet -> should error cleanly
