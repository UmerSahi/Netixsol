"""
data_layer.py
=============
Section 5 (Load and cache datasets) + Section 4 (schema inspection helpers).

Loads all four confirmed CSVs plus the optional feature manifest exactly
once and caches them at module level. No other module should call
pd.read_csv on these files directly.

CONFIRMED SCHEMA (inspected directly from the uploaded files -- nothing here
is assumed):

afl_match_retrieval.csv (7904 rows, 14 cols)
    match_id, season(int), round(str: '0'-'24','EF','QF','SF','PF','GF'),
    match_date(str 'YYYY-MM-DD'), venue, home_team, away_team,
    home_score(int), away_score(int), crowd(float, 199 nulls),
    margin(int), result('H'/'A'/'D'), home_win(float 0/1, NaN for draws),
    winner_team(str)

afl_player_retrieval.csv (217568 rows, 48 cols)
    id, team, year(int), career_game_count, opponent, round, result('W'/'L'/'D'),
    jersey_num, kicks, marks, handballs, disposals, goals, behinds, hit_outs,
    tackles, rebound_50s, inside_50s, clearances, clangers, free_kicks_for,
    free_kicks_against, brownlow_votes, contested_possessions,
    uncontested_possessions, contested_marks, marks_inside_50, one_percenters,
    bounces, goal_assist, percentage_of_game_played, player_id, match_date,
    fantasy_points, margin, player_score, disposals_raw, match_id, season,
    is_home(bool), is_match_top_disposals(0/1), is_match_top_goals(0/1),
    player_name, first_name, last_name, born_date, height, weight
    (player_name/first_name/last_name/born_date/height/weight have 1783 nulls
     -- some historical rows lack biographical data; handled defensively)

afl_match_prediction_features.csv (7904 rows, 76 cols)
    Same key columns as match_retrieval (match_id, season, round, match_date,
    venue, home_team, away_team) + home_win (TARGET, 65 NaN = draws) +
    70 leakage-safe pre-match features (home_*/away_* career, recent-form,
    season-form, home/away split, rest, venue and h2h priors) + 10 engineered
    difference features. Confirmed via feature_manifest.csv: every one of
    these is leakage_safe=True; only home_win is recommended_for_model=False
    (it's the target).

afl_player_prediction_features.csv (217568 rows, 94 cols)
    Same key/raw-stat columns as player_retrieval MINUS the biographical
    columns, PLUS engineered prior/rolling features and 6 target-like columns
    that the manifest marks recommended_for_model=False: disposals, goals,
    fantasy_points (regression targets, computed from the match just played)
    and is_match_top_disposals / is_match_top_goals (classification targets).
    All other engineered *_prior / rolling_* / *_prior_sum columns are
    leakage-safe pre-match features.

feature_manifest.csv (122 rows)
    dataset_name, feature_name, dtype, feature_category, description,
    source_columns, leakage_safe(bool), recommended_for_model(bool).
    Used as the authoritative list of which columns are safe model inputs.
"""
from __future__ import annotations
import os
import pandas as pd
import numpy as np
from functools import lru_cache

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Auto-detect the "data" subfolder next to this file (matches the standard
# project layout: data_layer.py and data/ side by side). AFL_DATA_DIR still
# overrides this if explicitly set, so nothing changes for anyone already
# using an env var.
DATA_DIR = os.environ.get("AFL_DATA_DIR", os.path.join(_THIS_DIR, "data"))

_FILES = {
    "match_retrieval": "afl_match_retrieval.csv",
    "player_retrieval": "afl_player_retrieval.csv",
    "match_features": "afl_match_prediction_features.csv",
    "player_features": "afl_player_prediction_features.csv",
    "manifest": "feature_manifest.csv",
}


def _load_dataset(name: str) -> pd.DataFrame:
    if name not in _FILES:
        raise KeyError(f"Unknown dataset '{name}'.")
    path = os.path.join(DATA_DIR, _FILES[name])
    if not os.path.exists(path):
        if name == "manifest":
            return None
        raise FileNotFoundError(
            f"Required dataset '{_FILES[name]}' not found at {path}. "
            f"The AFL agent cannot answer factual/prediction questions without it."
        )
    df = pd.read_csv(path)
    if name in ["match_retrieval", "player_retrieval", "match_features", "player_features"]:
        if "round" in df.columns:
            df["round"] = df["round"].astype(str)
    if name == "player_retrieval":
        for col in ["player_name", "first_name", "last_name"]:
            if col in df.columns:
                df[col] = df[col].str.strip()
    return df


@lru_cache(maxsize=None)
def get_dataset(name: str) -> pd.DataFrame:
    """Load and cache only the requested dataset."""
    return _load_dataset(name)


def load_all() -> dict:
    """
    Load every dataset exactly once (lru_cache ensures a single read even if
    called from many nodes/tools) and return them in a dict keyed the same
    way as _FILES. Missing optional files (feature_manifest) degrade
    gracefully; missing REQUIRED files raise a clear error instead of
    crashing deep inside a tool call later.
    """
    return {key: get_dataset(key) for key in _FILES}


@lru_cache(maxsize=1)
def get_manifest_safe_features(dataset_name: str) -> list:
    """
    Return the feature_manifest-confirmed list of leakage-safe,
    recommended-for-model columns for a given prediction dataset
    ('afl_match_prediction_features' or 'afl_player_prediction_features').
    Falls back to None if the manifest wasn't uploaded -- callers must then
    fall back to a manually-audited exclusion list (see model_training.py).
    """
    man = get_dataset("manifest")
    if man is None:
        return None
    sub = man[man["dataset_name"] == dataset_name]
    safe = sub[(sub["leakage_safe"] == True) & (sub["recommended_for_model"] == True)]
    return safe["feature_name"].tolist()


@lru_cache(maxsize=1)
def get_known_teams() -> tuple:
    """All 20 canonical team names as they appear in the data (both
    retrieval files agree on team naming -- confirmed by inspection)."""
    mr = get_dataset("match_retrieval")
    teams = set(mr["home_team"].unique()) | set(mr["away_team"].unique())
    return tuple(sorted(teams))


if __name__ == "__main__":
    d = load_all()
    for k, v in d.items():
        print(k, None if v is None else v.shape)
    print("Teams:", get_known_teams())
    print("Match model features (manifest):", len(get_manifest_safe_features("afl_match_prediction_features") or []))
    print("Player model features (manifest):", len(get_manifest_safe_features("afl_player_prediction_features") or []))
