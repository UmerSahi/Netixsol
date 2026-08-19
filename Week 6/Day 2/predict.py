
import joblib
import json
import numpy as np
import pandas as pd
import os
import sys

# Helper functions for feature engineering, included directly in predict.py
# since afl_lib.py does not seem to contain them in the execution environment.
def _team_long_format(match_df):
    home_df = match_df.copy()
    home_df = home_df.rename(columns=lambda c: c.replace('home_', 'team_').replace('away_', 'opp_') if c.startswith(('home_', 'away_')) else c)
    home_df['is_home'] = 1
    away_df = match_df.copy()
    away_df = away_df.rename(columns=lambda c: c.replace('away_', 'team_').replace('home_', 'opp_') if c.startswith(('home_', 'away_')) else c)
    away_df['is_home'] = 0
    return pd.concat([home_df, away_df], ignore_index=True)

def _get_rolling_team_features(team_match_long, team_name, current_date, window, stat):
    team_data = team_match_long[(team_match_long["team"] == team_name) &
                                 (team_match_long["match_date"] < current_date)].copy()
    if team_data.empty:
        return np.nan
    team_data = team_data.sort_values(by="match_date", ascending=True)
    if stat in team_data.columns and pd.api.types.is_numeric_dtype(team_data[stat]):
        rolling_avg = team_data[stat].rolling(window=window, min_periods=1).mean().iloc[-1]
    else:
        rolling_avg = np.nan
    return rolling_avg

def _get_h2h_features(team_match_long, team_name, opp_name, current_date, window):
    h2h_data = team_match_long[((team_match_long["team"] == team_name) & (team_match_long["opponent"] == opp_name) |
                                ((team_match_long["team"] == opp_name) & (team_match_long["opponent"] == team_name))) &
                               (team_match_long["match_date"] < current_date)].copy()
    if h2h_data.empty:
        return np.nan
    h2h_data = h2h_data.sort_values(by="match_date", ascending=True).tail(window)

    team_wins = (h2h_data[h2h_data["team"] == team_name]["win"] == 1).sum()
    opp_wins = (h2h_data[h2h_data["team"] == opp_name]["win"] == 1).sum()
    draws = (h2h_data["draw"] == 1).sum()

    return team_wins / (team_wins + opp_wins + draws) if (team_wins + opp_wins + draws) > 0 else np.nan


def _get_team_ladder_position(team_match_long, team_name, current_date):
    historical_matches = team_match_long[team_match_long["match_date"] < current_date].copy()
    if historical_matches.empty:
        return np.nan
    latest_season_before_match = historical_matches[historical_matches["team"] == team_name]["season"].max()
    if pd.isna(latest_season_before_match):
        return np.nan
    season_data = historical_matches[historical_matches["season"] == latest_season_before_match]
    latest_match_date_in_season = season_data[season_data["match_date"] < current_date]["match_date"].max()
    if pd.isna(latest_match_date_in_season):
        return np.nan
    season_data_up_to_date = season_data[season_data["match_date"] <= latest_match_date_in_season]
    ladder_data = season_data_up_to_date.groupby('team').agg(
        points=('points', 'sum'),
        score_for=('team_score', 'sum'),
        score_against=('opp_score', 'sum')
    ).reset_index()
    ladder_data['percentage'] = (ladder_data['score_for'] / ladder_data['score_against']) * 100
    ladder_data = ladder_data.sort_values(by=['points', 'percentage'], ascending=[False, False])
    ladder_data['ladder_position'] = ladder_data.reset_index().index + 1
    team_position = ladder_data[ladder_data["team"] == team_name]["ladder_position"]
    return team_position.iloc[0] if not team_position.empty else np.nan

def _get_team_features_for_match(team_match_long, match_date, home_team, away_team, metadata):
    match_features_dict = {}
    windows = [3, 5, 10]
    rolling_stats_score = ["score_for", "score_against", "margin"]
    rolling_stats_winrate = ["win"]
    current_season = pd.to_datetime(match_date).year
    for team, prefix in [(home_team, "home_"), (away_team, "away_")]:
        for window in windows:
            for stat in rolling_stats_score:
                feature_name = f"{prefix}form_avg_{stat}_last{window}"
                match_features_dict[feature_name] = _get_rolling_team_features(
                    team_match_long, team, match_date, window, f"team_{stat}"
                )
            for stat in rolling_stats_winrate:
                feature_name = f"{prefix}form_win_rate_last{window}"
                match_features_dict[feature_name] = _get_rolling_team_features(
                    team_match_long, team, match_date, window, f"team_{stat}"
                )
        match_features_dict[f"{prefix}team_streak_entering_game"] = np.nan
        team_matches_before = team_match_long[(team_match_long["team"] == team) & (team_match_long["match_date"] < match_date)].sort_values("match_date")
        if not team_matches_before.empty:
            last_match_date = team_matches_before["match_date"].iloc[-1]
            match_features_dict[f"{prefix}days_since_last_match"] = (pd.to_datetime(match_date) - last_match_date).days
        else:
            match_features_dict[f"{prefix}days_since_last_match"] = np.nan
        team_games_in_season = team_match_long[(team_match_long["team"] == team) &
                                               (team_match_long["season"] == current_season) &
                                               (team_match_long["match_date"] < match_date)]
        match_features_dict[f"{prefix}games_played_in_season"] = len(team_games_in_season)
        match_features_dict[f"{prefix}ladder_position_prior"] = _get_team_ladder_position(team_match_long, team, match_date)
        match_features_dict[f"{prefix}percentage_prior"] = np.nan
        match_features_dict[f"{prefix}venue_experience"] = np.nan
    match_features_dict["h2h_win_rate_last5"] = _get_h2h_features(team_match_long, home_team, away_team, match_date, 5)
    match_features_dict["ladder_position_diff"] = match_features_dict["home_ladder_position_prior"] - match_features_dict["away_ladder_position_prior"]
    features_df = pd.DataFrame([match_features_dict])
    features_df["venue"] = metadata["default_venue"]
    features_df["home_team"] = home_team
    features_df["away_team"] = away_team
    features_df["round"] = metadata["default_round"]
    for col in metadata["match_feature_columns"]:
        if col not in features_df.columns:
            features_df[col] = np.nan
    return features_df[metadata["match_feature_columns"]]

def _get_player_features_for_match(player_features_df, match_id, metadata):
    match_players = player_features_df[player_features_df["match_id"] == match_id].copy()
    if match_players.empty:
        raise ValueError(f"Match ID '{match_id}' not found in historical player data.")
    return match_players[metadata["player_feature_columns"] + ["player_id", "fantasy_points", "disposals"]]

def _get_player_features_for_team(player_latest_state, team_name, metadata):
    team_players = player_latest_state[player_latest_state["team"] == team_name].copy()
    if team_players.empty:
        raise ValueError(f"Team '{team_name}' not found in latest player states.")
    return team_players[metadata["player_feature_columns"] + ["player_id"]]

# From clean_teams.py (CANONICAL_MAP)
CANONICAL_MAP = {
    "Adelaide Crows": ["adelaide crows"],
    "Brisbane Bears": ["brisbane bears"],
    "Brisbane Lions": ["brisbane lions"],
    "Carlton Blues": ["carlton blues"],
    "Collingwood Magpies": ["collingwood magpies"],
    "Essendon Bombers": ["essendon bombers"],
    "Fitzroy Lions": ["fitzroy lions"],
    "Fremantle Dockers": ["fremantle dockers"],
    "Geelong Cats": ["geelong cats"],
    "Gold Coast Suns": ["gold coast suns"],
    "Greater Western Sydney Giants": ["greater western sydney giants", "gws giants", "gws"],
    "Hawthorn Hawks": ["hawthorn hawks"],
    "Melbourne Demons": ["melbourne demons"],
    "North Melbourne Kangaroos": ["north melbourne kangaroos", "kangaroos"],
    "Port Adelaide Power": ["port adelaide power"],
    "Richmond Tigers": ["richmond tigers"],
    "St Kilda Saints": ["st kilda saints"],
    "Sydney Swans": ["sydney swans"],
    "West Coast Eagles": ["west coast eagles"],
    "Western Bulldogs": ["western bulldogs", "footscray bulldogs"],
}

def _clean_team_name(team_name, canonical_map):
    for canonical, aliases in canonical_map.items():
        if team_name.lower() in [a.lower() for a in aliases]:
            return canonical
    return None

# Load artifacts
_MATCH_WINNER_PIPELINE = joblib.load("match_winner_pipeline.joblib")
_PLAYER_FANTASY_POINTS_PIPELINE = joblib.load("player_fantasy_points_pipeline.joblib")
_PLAYER_DISPOSALS_PIPELINE = joblib.load("player_disposals_pipeline.joblib")

with open("model_metadata.json", "r") as f:
    _MODEL_METADATA = json.load(f)

_TEAM_MATCH_LONG_DF = pd.read_csv("team_match_long.csv", parse_dates=["match_date"])
_PLAYER_LATEST_STATE_DF = pd.read_csv("player_latest_state.csv", parse_dates=["match_date"])
_PLAYER_GAME_FEATURES_RETRO_DF = pd.read_csv("afl_player_game_features_v1.csv.gz", compression="gzip", parse_dates=["match_date"])


def predict_match_winner(home_team: str, away_team: str, match_date: str, venue: str | None = None) -> dict:
    match_date = pd.to_datetime(match_date)
    home_team_canonical = _clean_team_name(home_team, CANONICAL_MAP)
    away_team_canonical = _clean_team_name(away_team, CANONICAL_MAP)
    if not home_team_canonical:
        raise ValueError(f"Unrecognised home team: '{home_team}'. Valid teams: {_MODEL_METADATA['valid_teams']}")
    if not away_team_canonical:
        raise ValueError(f"Unrecognised away team: '{away_team}'. Valid teams: {_MODEL_METADATA['valid_teams']}")

    if home_team_canonical == away_team_canonical:
        raise ValueError("Home and away teams cannot be the same.")

    if match_date < pd.to_datetime(_MODEL_METADATA["data_date_range"][0]):
        raise ValueError(f"Match date '{match_date.date()}' is before earliest training data: {_MODEL_METADATA['data_date_range'][0]}")

    feature_df = _get_team_features_for_match(_TEAM_MATCH_LONG_DF, match_date, home_team_canonical, away_team_canonical, _MODEL_METADATA)

    if venue:
        if venue not in _MODEL_METADATA['valid_venues']:
            raise ValueError(f"Unrecognised venue: '{venue}'. Valid venues: {_MODEL_METADATA['valid_venues']}")
        feature_df['venue'] = venue
    else:
        feature_df['venue'] = _MODEL_METADATA['default_venue']

    home_win_proba = _MATCH_WINNER_PIPELINE.predict_proba(feature_df[_MODEL_METADATA["match_feature_columns"]])[:, 1][0]

    predicted_winner = home_team_canonical if home_win_proba >= 0.5 else away_team_canonical

    return {
        "home_team": home_team_canonical,
        "away_team": away_team_canonical,
        "match_date": str(match_date.date()),
        "predicted_winner": predicted_winner,
        "home_win_probability": round(home_win_proba, 3),
        "away_win_probability": round(1 - home_win_proba, 3),
        "model_version": _MODEL_METADATA["match_model_version"]
    }

def predict_top_player(match_id: str | None = None, team: str | None = None,
                        stat_type: str = "fantasy_points", top_n: int = 5,
                        opponent: str | None = None) -> list[dict]:
    if not match_id and not team:
        raise ValueError("Either 'match_id' or 'team' must be provided.")
    if match_id and team:
        print("Warning: Both match_id and team provided. Using match_id for retrospective prediction.")

    if stat_type not in ["fantasy_points", "disposals"]:
        raise ValueError(f"Unsupported stat_type: '{stat_type}'. Supported types: 'fantasy_points', 'disposals'.")

    pipeline_to_use = _PLAYER_FANTASY_POINTS_PIPELINE if stat_type == "fantasy_points" else _PLAYER_DISPOSALS_PIPELINE

    if match_id: # Retrospective mode
        player_data = _get_player_features_for_match(_PLAYER_GAME_FEATURES_RETRO_DF, match_id, _MODEL_METADATA)
        preds = pipeline_to_use.predict(player_data[_MODEL_METADATA["player_feature_columns"]])
        player_data[f"predicted_{stat_type}"] = preds
        player_data = player_data.sort_values(by=f"predicted_{stat_type}", ascending=False).head(top_n)

        results = []
        for _, row in player_data.iterrows():
            results.append({
                "player_id": row["player_id"],
                "team": row["team"],
                "predicted_score": round(row[f"predicted_{stat_type}"], 2),
                "actual_score": row[stat_type],
                "stat_type": stat_type
            })
        return results

    elif team:
        team_canonical = _clean_team_name(team, CANONICAL_MAP)
        if not team_canonical:
            raise ValueError(f"Unrecognised team: '{team}'. Valid teams: {_MODEL_METADATA['valid_teams']}")

        player_data = _get_player_features_for_team(_PLAYER_LATEST_STATE_DF, team_canonical, _MODEL_METADATA)
        preds = pipeline_to_use.predict(player_data[_MODEL_METADATA["player_feature_columns"]])
        player_data[f"predicted_{stat_type}"] = preds
        player_data = player_data.sort_values(by=f"predicted_{stat_type}", ascending=False).head(top_n)

        results = []
        for _, row in player_data.iterrows():
            results.append({
                "player_id": row["player_id"],
                "team": row["team"],
                "predicted_score": round(row[f"predicted_{stat_type}"], 2),
                "stat_type": stat_type
            })
        return results
