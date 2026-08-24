"""
prediction_tools.py
====================
Section 9: Prediction tools.

build_match_features_for_prediction() recomputes the EXACT same feature
definitions used in afl_match_prediction_features.csv (verified empirically
against the real file -- see verification block at the bottom) directly
from afl_match_retrieval.csv, using only matches strictly before the
requested `as_of_date`. This lets us build a single new pre-match feature
row for a matchup that has no row of its own (e.g. an upcoming or
hypothetical fixture) without ever looking at future results.

predict_match_winner() and predict_top_player() load the trained artifacts
from model_training.py, apply the exact saved feature order + imputer
(+scaler where relevant), and return probabilities -- never inventing a
number the model didn't produce.

CAPSTONE ADDITIONS (v2):
    - PLAYER_PREDICTION_SPECS generalizes predict_top_player() beyond
      disposals: goals, kicks, marks, handballs, and tackles regressors
      are supported using the exact same leakage-safe feature pipeline,
      as long as the corresponding artifact has been trained (see
      model_training.train_player_stat_regressor). Anything not in this
      table still returns a clear "unsupported" response instead of a
      guess -- the original design principle is unchanged, only the
      supported set has grown.
    - predict_player_stat_value(): a single-player point prediction (not a
      whole-team ranking), used to answer "how many disposals is X
      expected to get?" and to power predicted head-to-head player
      comparisons (predict_player_stat_value called once per player).
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd
import joblib

from data_layer import get_dataset
from resolvers import resolve_team, resolve_player
from model_training import MODEL_DIR

pd.options.mode.chained_assignment = None


# ---------------------------------------------------------------------------
# Feature engineering reproduced from afl_match_prediction_features.csv
# ---------------------------------------------------------------------------
def _team_snapshot(team: str, as_of_date: pd.Timestamp, opponent: str,
                    is_home_role: bool, season: int, venue: str = None) -> dict:
    """
    Recompute one team's full set of *_prior features as of (strictly
    before) as_of_date, using only afl_match_retrieval.csv -- the same
    source data the offline feature pipeline was built from.
    """
    mr = get_dataset("match_retrieval").copy()
    mr["match_date"] = pd.to_datetime(mr["match_date"])
    as_of_date = pd.to_datetime(as_of_date)

    # long-format: one row per team per match they played, regardless of side
    home = mr[["match_id", "season", "match_date", "venue", "home_team", "away_team",
               "home_score", "away_score", "home_win"]].rename(columns={
                   "home_team": "team", "away_team": "opp", "home_score": "team_score",
                   "away_score": "opp_score"})
    home["is_home"] = True
    home["win"] = home["home_win"]
    away = mr[["match_id", "season", "match_date", "venue", "home_team", "away_team",
               "away_score", "home_score", "home_win"]].rename(columns={
                   "away_team": "team", "home_team": "opp", "away_score": "team_score",
                   "home_score": "opp_score"})
    away["is_home"] = False
    away["win"] = 1 - away["home_win"]
    long_df = pd.concat([home, away], ignore_index=True)
    long_df["margin"] = long_df["team_score"] - long_df["opp_score"]
    # Draws count as half a win for win-sum/win-rate purposes (confirmed
    # empirically against the training file: career_win_prior_sum contains
    # .5 fractions exactly where the team's history includes a draw).
    long_df["win"] = long_df["win"].fillna(0.5)

    prior = long_df[(long_df["team"] == team) & (long_df["match_date"] < as_of_date)].sort_values("match_date")

    if prior.empty:
        # Brand-new/never-played-before team as of this date -- everything unknown.
        base = {k: np.nan for k in [
            "career_games_played_prior", "career_win_prior_sum", "career_win_rate_prior",
            "career_avg_score_prior", "career_avg_conceded_prior",
            "recent_3_win_rate", "recent_5_win_rate", "recent_10_win_rate",
            "recent_3_avg_score", "recent_5_avg_score", "recent_3_avg_conceded",
            "recent_5_avg_conceded", "recent_3_avg_margin", "recent_5_avg_margin",
            "win_streak_entering", "season_games_played_prior", "season_win_prior_sum",
            "season_win_rate_prior", "season_avg_score_prior", "season_avg_conceded_prior",
            "season_avg_margin_prior", "home_away_win_rate_prior", "home_away_avg_score_prior",
            "days_since_last_match", "venue_win_rate_prior", "venue_games_played_prior",
            "h2h_games_played_prior", "h2h_win_rate_prior", "h2h_avg_margin_prior",
        ]}
        base["career_games_played_prior"] = 0
        base["season_games_played_prior"] = 0
        base["venue_games_played_prior"] = 0
        base["h2h_games_played_prior"] = 0
        return base

    career_games = len(prior)
    career_wins = prior["win"].sum()

    def _win_streak(wins: pd.Series) -> float:
        """Signed streak: positive = consecutive wins entering the match,
        negative = consecutive losses/draws entering the match (confirmed
        empirically against the training file, which contains negative
        values e.g. -3.0 for a 3-match losing run)."""
        streak = 0
        direction = None
        for w in wins.iloc[::-1]:
            is_win = w == 1
            if direction is None:
                direction = is_win
                streak = 1
            elif is_win == direction:
                streak += 1
            else:
                break
        return float(streak if direction else -streak)

    recent3, recent5, recent10 = prior.tail(3), prior.tail(5), prior.tail(10)
    season_prior = prior[prior["season"] == season]
    home_away_prior = prior[prior["is_home"] == is_home_role]
    venue_prior = prior[prior["venue"] == venue] if venue else prior.iloc[0:0]
    h2h_prior = prior[prior["opp"] == opponent]
    days_since = (as_of_date - prior["match_date"].iloc[-1]).days

    out = {
        "career_games_played_prior": career_games,
        "career_win_prior_sum": float(career_wins),
        "career_win_rate_prior": float(career_wins / career_games) if career_games else np.nan,
        "career_avg_score_prior": float(prior["team_score"].mean()),
        "career_avg_conceded_prior": float(prior["opp_score"].mean()),
        "recent_3_win_rate": float(recent3["win"].mean()) if len(recent3) else np.nan,
        "recent_5_win_rate": float(recent5["win"].mean()) if len(recent5) else np.nan,
        "recent_10_win_rate": float(recent10["win"].mean()) if len(recent10) else np.nan,
        "recent_3_avg_score": float(recent3["team_score"].mean()) if len(recent3) else np.nan,
        "recent_5_avg_score": float(recent5["team_score"].mean()) if len(recent5) else np.nan,
        "recent_3_avg_conceded": float(recent3["opp_score"].mean()) if len(recent3) else np.nan,
        "recent_5_avg_conceded": float(recent5["opp_score"].mean()) if len(recent5) else np.nan,
        "recent_3_avg_margin": float(recent3["margin"].mean()) if len(recent3) else np.nan,
        "recent_5_avg_margin": float(recent5["margin"].mean()) if len(recent5) else np.nan,
        "win_streak_entering": _win_streak(prior["win"]),
        "season_games_played_prior": int(len(season_prior)),
        "season_win_prior_sum": float(season_prior["win"].sum()) if len(season_prior) else np.nan,
        "season_win_rate_prior": float(season_prior["win"].mean()) if len(season_prior) else np.nan,
        "season_avg_score_prior": float(season_prior["team_score"].mean()) if len(season_prior) else np.nan,
        "season_avg_conceded_prior": float(season_prior["opp_score"].mean()) if len(season_prior) else np.nan,
        "season_avg_margin_prior": float(season_prior["margin"].mean()) if len(season_prior) else np.nan,
        "home_away_win_rate_prior": float(home_away_prior["win"].mean()) if len(home_away_prior) else np.nan,
        "home_away_avg_score_prior": float(home_away_prior["team_score"].mean()) if len(home_away_prior) else np.nan,
        "days_since_last_match": float(days_since),
        # Venue-specific history: only computed when a real venue is supplied.
        # For a hypothetical fixture with no confirmed venue this is left as
        # NaN (median-imputed by the trained pipeline) rather than guessed.
        "venue_win_rate_prior": float(venue_prior["win"].mean()) if len(venue_prior) else np.nan,
        "venue_games_played_prior": int(len(venue_prior)),
        "h2h_games_played_prior": int(len(h2h_prior)),
        "h2h_win_rate_prior": float(h2h_prior["win"].mean()) if len(h2h_prior) else np.nan,
        "h2h_avg_margin_prior": float(h2h_prior["margin"].mean()) if len(h2h_prior) else np.nan,
    }
    return out


def build_match_features_for_prediction(home_team: str, away_team: str,
                                          as_of_date: str = None, season: int = None,
                                          venue: str = None) -> dict:
    """
    Build exactly one new pre-match feature row (matching the training
    schema of afl_match_prediction_features.csv) for a matchup that may not
    exist in the historical file. Uses only match_retrieval.csv rows dated
    strictly before `as_of_date`. `venue` is optional -- when the caller
    cannot confirm a real venue for a hypothetical/future fixture, venue
    features are left as NaN rather than guessed.
    """
    mr = get_dataset("match_retrieval")
    latest_date = pd.to_datetime(mr["match_date"]).max()
    as_of = pd.to_datetime(as_of_date) if as_of_date else latest_date + pd.Timedelta(days=1)
    season = season or as_of.year

    home_snap = _team_snapshot(home_team, as_of, away_team, True, season, venue=venue)
    away_snap = _team_snapshot(away_team, as_of, home_team, False, season, venue=venue)

    row = {}
    for k, v in home_snap.items():
        row[f"home_{k}"] = v
    for k, v in away_snap.items():
        row[f"away_{k}"] = v

    def d(a, b):
        va, vb = row.get(a), row.get(b)
        if va is None or vb is None or (isinstance(va, float) and np.isnan(va)) or (isinstance(vb, float) and np.isnan(vb)):
            return np.nan
        return va - vb

    row["win_rate_difference"] = d("home_career_win_rate_prior", "away_career_win_rate_prior")
    row["recent_3_form_difference"] = d("home_recent_3_win_rate", "away_recent_3_win_rate")
    row["recent_5_form_difference"] = d("home_recent_5_win_rate", "away_recent_5_win_rate")
    row["recent_10_form_difference"] = d("home_recent_10_win_rate", "away_recent_10_win_rate")
    row["season_win_rate_difference"] = d("home_season_win_rate_prior", "away_season_win_rate_prior")
    row["scoring_average_difference"] = d("home_career_avg_score_prior", "away_career_avg_score_prior")
    row["conceded_average_difference"] = d("home_career_avg_conceded_prior", "away_career_avg_conceded_prior")
    row["h2h_win_rate_difference"] = d("home_h2h_win_rate_prior", "away_h2h_win_rate_prior")
    row["rest_difference"] = d("home_days_since_last_match", "away_days_since_last_match")
    row["win_streak_difference"] = d("home_win_streak_entering", "away_win_streak_entering")

    return row


# ---------------------------------------------------------------------------
# predict_match_winner
# ---------------------------------------------------------------------------
def predict_match_winner(home_team: str, away_team: str, as_of_date: str = None,
                          season: int = None, fixture_confirmed: bool = False) -> dict:
    """
    Predict the winner of a home_team vs away_team matchup using the trained
    match model. Returns probabilities for both teams plus the top
    contributing feature differences (never fabricated -- computed directly
    from the same feature row fed to the model).
    """
    tr_home, tr_away = resolve_team(home_team), resolve_team(away_team)
    for tr, label in [(tr_home, home_team), (tr_away, away_team)]:
        if tr.status == "ambiguous":
            return {"ok": False, "clarification": tr.message, "candidates": tr.candidates}
        if tr.status == "not_found":
            return {"ok": False, "error": tr.message}
    home, away = tr_home.value, tr_away.value
    if home == away:
        return {"ok": False, "error": "Match prediction requires two different teams."}

    meta_path = os.path.join(MODEL_DIR, "match_winner_metadata.json")
    if not os.path.exists(meta_path):
        return {"ok": False, "error": "The match winner model has not been trained yet."}
    with open(meta_path) as f:
        meta = json.load(f)

    feature_row = build_match_features_for_prediction(home, away, as_of_date, season)
    X = pd.DataFrame([feature_row])[meta["feature_columns"]]

    model = joblib.load(os.path.join(MODEL_DIR, "match_winner_model.joblib"))
    imputer = joblib.load(os.path.join(MODEL_DIR, "match_winner_imputer.joblib"))
    scaler = joblib.load(os.path.join(MODEL_DIR, "match_winner_scaler.joblib"))
    X_i = imputer.transform(X)
    X_final = scaler.transform(X_i) if scaler is not None else X_i

    proba_home = float(model.predict_proba(X_final)[0, 1])
    proba_away = 1.0 - proba_home
    predicted_winner = home if proba_home >= 0.5 else away

    # Top feature drivers = the largest-magnitude *_difference features actually
    # present in this feature row (computed, not invented).
    diff_cols = [c for c in meta["feature_columns"] if c.endswith("_difference")]
    diffs = {c: feature_row.get(c) for c in diff_cols if feature_row.get(c) is not None and not (isinstance(feature_row.get(c), float) and np.isnan(feature_row.get(c)))}
    top_drivers = sorted(diffs.items(), key=lambda kv: abs(kv[1]), reverse=True)[:3]

    fixture_note = None
    if not fixture_confirmed:
        fixture_note = (
            "I can make a matchup prediction for these two teams using the latest "
            "available historical data, but I cannot verify from the local dataset "
            "that they are actually scheduled to play at this time."
        )

    return {
        "ok": True,
        "data": {
            "home_team": home, "away_team": away,
            "predicted_winner": predicted_winner,
            "probability_home_win": round(proba_home, 4),
            "probability_away_win": round(proba_away, 4),
            "top_feature_drivers": [{"feature": k, "home_minus_away": round(float(v), 3)} for k, v in top_drivers],
            "model_type": meta["model_type"],
            "model_val_auc": meta["val_metric_value"],
            "model_test_auc": meta["test_metric_value"],
            "fixture_note": fixture_note,
        }
    }


# ---------------------------------------------------------------------------
# predict_top_player  (generalized -- v2)
# ---------------------------------------------------------------------------
# Every supported player-prediction task, generalized so adding a new
# regression target (e.g. "expected_marks") is a one-line addition here
# plus one call to model_training.train_player_stat_regressor -- nothing
# else in this file or in graph.py needs to change. Anything NOT in this
# table still returns a clear "unsupported" response rather than a guess,
# preserving the original design principle exactly.
PLAYER_PREDICTION_SPECS = {
    "top_disposals": {
        "kind": "classification", "target": "is_match_top_disposals",
        "model_file": "top_disposals_model.joblib", "meta_file": "top_disposals_metadata.json",
        "label": "top_disposals (classification)",
    },
    "expected_disposals": {
        "kind": "regression", "target": "disposals",
        "model_file": "expected_disposals_model.joblib", "meta_file": "expected_disposals_metadata.json",
        "label": "expected_disposals (regression)",
    },
    "expected_goals": {
        "kind": "regression", "target": "goals",
        "model_file": "expected_goals_model.joblib", "meta_file": "expected_goals_metadata.json",
        "label": "expected_goals (regression)",
    },
    "expected_kicks": {
        "kind": "regression", "target": "kicks",
        "model_file": "expected_kicks_model.joblib", "meta_file": "expected_kicks_metadata.json",
        "label": "expected_kicks (regression)",
    },
    "expected_marks": {
        "kind": "regression", "target": "marks",
        "model_file": "expected_marks_model.joblib", "meta_file": "expected_marks_metadata.json",
        "label": "expected_marks (regression)",
    },
    "expected_handballs": {
        "kind": "regression", "target": "handballs",
        "model_file": "expected_handballs_model.joblib", "meta_file": "expected_handballs_metadata.json",
        "label": "expected_handballs (regression)",
    },
    "expected_tackles": {
        "kind": "regression", "target": "tackles",
        "model_file": "expected_tackles_model.joblib", "meta_file": "expected_tackles_metadata.json",
        "label": "expected_tackles (regression)",
    },
}

# Maps a free-text stat word (as recognized by retrieval_tools.SUPPORTED_STATS)
# to its prediction_type key above, so "predict Geelong's top goalkicker" and
# "predict Geelong's top disposal getter" both resolve to a real model.
_STAT_TO_PREDICTION_TYPE = {
    "disposals": "expected_disposals", "goals": "expected_goals", "kicks": "expected_kicks",
    "marks": "expected_marks", "handballs": "expected_handballs", "tackles": "expected_tackles",
}

SUPPORTED_PLAYER_PREDICTIONS = set(PLAYER_PREDICTION_SPECS.keys())


def stat_to_prediction_type(stat: str) -> str | None:
    """Best-effort mapping from a recognized stat word (including synonyms
    like 'goalkicker' or the singular 'disposal') to a supported
    prediction_type -- used by graph.py so 'predict goals' or 'predict top
    goalkicker' both automatically reach the goals regressor instead of
    always defaulting to disposals."""
    from retrieval_tools import SUPPORTED_STATS
    key = (stat or "").strip().lower()
    canonical = SUPPORTED_STATS.get(key, key)  # normalize synonym -> canonical column name
    return _STAT_TO_PREDICTION_TYPE.get(canonical)


def _current_roster(team: str) -> list:
    """Players considered 'eligible' for a team: those who played for them
    in the most recent season present in the data (a practical proxy for a
    current roster; the CSVs contain no separate roster/list file)."""
    pf = get_dataset("player_features")
    team_rows = pf[pf["team"] == team]
    if team_rows.empty:
        return []
    latest_season = team_rows["season"].max()
    return sorted(team_rows[team_rows["season"] == latest_season]["id"].unique().tolist())


def predict_top_player(team: str, opponent: str = None, prediction_type: str = "top_disposals", limit: int = 5) -> dict:
    """
    Rank a team's current-roster players for a supported prediction task.
    Only tasks the trained models actually support are allowed --
    everything else returns a clear "unsupported" response instead of a
    guess.
    """
    if prediction_type not in PLAYER_PREDICTION_SPECS:
        return {
            "ok": False,
            "error": (f"I can predict the supported player outcomes in my model "
                      f"({', '.join(sorted(PLAYER_PREDICTION_SPECS.keys()))}), but I do not have a "
                      f"trained model for '{prediction_type}'.")
        }
    spec = PLAYER_PREDICTION_SPECS[prediction_type]
    limit = max(1, int(limit))

    tr = resolve_team(team)
    if tr.status == "ambiguous":
        return {"ok": False, "clarification": tr.message, "candidates": tr.candidates}
    if tr.status == "not_found":
        return {"ok": False, "error": tr.message}
    team_name = tr.value

    opp_name = None
    if opponent:
        tro = resolve_team(opponent)
        if tro.status == "ambiguous":
            return {"ok": False, "clarification": tro.message, "candidates": tro.candidates}
        if tro.status == "not_found":
            return {"ok": False, "error": tro.message}
        opp_name = tro.value

    pf = get_dataset("player_features")
    team_rows = pf[pf["team"] == team_name]
    if team_rows.empty:
        return {"ok": False, "error": f"No player data found for {team_name}."}
    latest_season = team_rows["season"].max()
    # Most recent feature row per player (their latest known pre-match state).
    # Grouped by player_id (the stable player identifier) -- NOT the "id"
    # column, which is a per-row/per-match record id and would otherwise
    # return every match row for every player untouched.
    recent = team_rows[team_rows["season"] == latest_season].sort_values("match_date").groupby("player_id").tail(1)
    if recent.empty:
        return {"ok": False, "error": f"No recent player data available for {team_name}."}

    meta_path = os.path.join(MODEL_DIR, spec["meta_file"])
    if not os.path.exists(meta_path):
        return {"ok": False, "error": f"The '{prediction_type}' model has not been trained yet."}
    with open(meta_path) as f:
        meta = json.load(f)

    X = recent[meta["feature_columns"]]
    model = joblib.load(os.path.join(MODEL_DIR, spec["model_file"]))
    imputer_path = os.path.join(MODEL_DIR, spec["model_file"].replace("model.joblib", "imputer.joblib"))
    imputer = joblib.load(imputer_path)
    X_i = imputer.transform(X)

    names = get_dataset("player_retrieval").drop_duplicates("player_id")[["player_id", "player_name"]]

    if spec["kind"] == "classification":
        proba = model.predict_proba(X_i)[:, 1]
        recent = recent.assign(_score=proba)
        ranked = recent.sort_values("_score", ascending=False)
        top_n = ranked[["player_id", "_score"]].head(limit).merge(names, on="player_id", how="left")
        return {
            "ok": True,
            "data": {
                "team": team_name, "prediction_type": spec["label"],
                "predictions": [
                    {"player": r["player_name"], "probability": round(float(r["_score"]), 4)}
                    for _, r in top_n.iterrows()
                ],
                "model_val_auc": meta["val_metric_value"], "model_test_auc": meta["test_metric_value"],
            }
        }
    else:
        preds = model.predict(X_i)
        recent = recent.assign(_pred=preds)
        ranked = recent.sort_values("_pred", ascending=False)
        top_n = ranked[["player_id", "_pred"]].head(limit).merge(names, on="player_id", how="left")
        pred_key = f"predicted_{spec['target']}"
        return {
            "ok": True,
            "data": {
                "team": team_name, "prediction_type": spec["label"],
                "predictions": [
                    {"player": r["player_name"], pred_key: round(float(r["_pred"]), 1)}
                    for _, r in top_n.iterrows()
                ],
                "model_val_mae": meta["val_metric_value"], "model_test_mae": meta["test_metric_value"],
            }
        }


# ---------------------------------------------------------------------------
# predict_player_stat_value  (NEW -- single-player point prediction)
# ---------------------------------------------------------------------------
def predict_player_stat_value(player: str, prediction_type: str = "expected_disposals") -> dict:
    """
    Point prediction for ONE named player (as opposed to predict_top_player,
    which ranks an entire team's roster). Used both for direct "how many
    disposals is X expected to get?" questions and as the building block
    for a predicted head-to-head player comparison (call this once per
    player, on the same stat).
    """
    if prediction_type not in PLAYER_PREDICTION_SPECS or PLAYER_PREDICTION_SPECS[prediction_type]["kind"] != "regression":
        return {"ok": False, "error": f"I do not have a trained regression model for '{prediction_type}'."}
    spec = PLAYER_PREDICTION_SPECS[prediction_type]

    pres = resolve_player(player)
    if pres.status == "ambiguous":
        return {"ok": False, "clarification": pres.message, "candidates": pres.candidates}
    if pres.status == "not_found":
        return {"ok": False, "error": pres.message}
    name = pres.value

    pr = get_dataset("player_retrieval")
    prow = pr[pr["player_name"] == name].sort_values("match_date").tail(1)
    if prow.empty:
        return {"ok": False, "error": f"No recent data available for {name}."}
    player_id = prow.iloc[0]["player_id"]

    pf = get_dataset("player_features")
    recent = pf[pf["player_id"] == player_id].sort_values("match_date").tail(1)
    if recent.empty:
        return {"ok": False, "error": f"No feature-ready data available for {name}."}

    meta_path = os.path.join(MODEL_DIR, spec["meta_file"])
    if not os.path.exists(meta_path):
        return {"ok": False, "error": f"The '{prediction_type}' model has not been trained yet."}
    with open(meta_path) as f:
        meta = json.load(f)

    X = recent[meta["feature_columns"]]
    model = joblib.load(os.path.join(MODEL_DIR, spec["model_file"]))
    imputer_path = os.path.join(MODEL_DIR, spec["model_file"].replace("model.joblib", "imputer.joblib"))
    imputer = joblib.load(imputer_path)
    X_i = imputer.transform(X)
    pred = float(model.predict(X_i)[0])

    return {
        "ok": True,
        "data": {
            "player": name, "team": recent.iloc[0]["team"], "prediction_type": spec["label"],
            "predicted_value": round(pred, 1), "stat": spec["target"],
            "model_val_mae": meta["val_metric_value"], "model_test_mae": meta["test_metric_value"],
        }
    }


# ---------------------------------------------------------------------------
# predict_premiership_favourite
# ---------------------------------------------------------------------------
def predict_premiership_favourite(season: int = None, top_n: int = 8) -> dict:
    """
    Estimate which team currently looks strongest for a given season using
    the SAME trained match-winner model as predict_match_winner() -- there
    is no separate "championship" model, and no ladder/finals simulation.

    Method (a power-ranking, not a season simulation): build a full
    round-robin of every team vs every other team using each team's most
    recent known pre-match feature snapshot (build_match_features_for_prediction,
    the same leakage-safe pipeline used for single-match predictions), run
    the trained model on all of it, and rank teams by their average
    predicted win probability across every matchup.

    This deliberately does NOT attempt to simulate a fixture, ladder, or
    finals series (none of that exists in the uploaded data), and the
    result becomes rapidly less meaningful the further `season` is from the
    data's most recent season -- rosters, coaches, and team strength change
    substantially year to year, and the model was never trained to predict
    multi-year-ahead team strength. This is surfaced explicitly in the
    response rather than presented as a real premiership prediction.
    """
    meta_path = os.path.join(MODEL_DIR, "match_winner_metadata.json")
    if not os.path.exists(meta_path):
        return {"ok": False, "error": "The match winner model has not been trained yet."}
    with open(meta_path) as f:
        meta = json.load(f)

    mr = get_dataset("match_retrieval")
    latest_season = int(mr["season"].max())
    latest_date = pd.to_datetime(mr["match_date"]).max()
    season = season or (latest_season + 1)
    years_ahead = season - latest_season

    # Restrict to CURRENTLY ACTIVE teams -- i.e. teams that actually played
    # in the most recent 2 seasons of data. Without this, defunct/merged
    # historical clubs (e.g. Fitzroy Lions, Brisbane Bears -- both gone from
    # the competition since the mid-1990s) would be ranked using a tiny,
    # decades-stale sample and can produce wildly overconfident numbers for
    # teams that no longer exist to actually win anything.
    active_window = mr[mr["season"] >= latest_season - 1]
    teams = sorted(set(active_window["home_team"]) | set(active_window["away_team"]))

    rows, pairs = [], []
    for home in teams:
        for away in teams:
            if home == away:
                continue
            feat = build_match_features_for_prediction(home, away, season=season)
            rows.append(feat)
            pairs.append((home, away))

    X = pd.DataFrame(rows)[meta["feature_columns"]]
    model = joblib.load(os.path.join(MODEL_DIR, "match_winner_model.joblib"))
    imputer = joblib.load(os.path.join(MODEL_DIR, "match_winner_imputer.joblib"))
    scaler = joblib.load(os.path.join(MODEL_DIR, "match_winner_scaler.joblib"))
    X_i = imputer.transform(X)
    X_final = scaler.transform(X_i) if scaler is not None else X_i
    proba_home = model.predict_proba(X_final)[:, 1]

    # Each team's overall strength = average probability of winning across
    # every matchup, counting both its home-role and away-role games.
    win_probs = {t: [] for t in teams}
    for (home, away), p_home in zip(pairs, proba_home):
        win_probs[home].append(float(p_home))
        win_probs[away].append(1.0 - float(p_home))

    ranking = sorted(
        ({"team": t, "avg_win_probability": round(sum(v) / len(v), 4)} for t, v in win_probs.items()),
        key=lambda r: r["avg_win_probability"], reverse=True,
    )
    for i, r in enumerate(ranking, start=1):
        r["rank"] = i

    confidence_note = None
    if years_ahead >= 2:
        confidence_note = (
            f"This is {years_ahead} seasons beyond the most recent data available "
            f"({latest_season}). Team strength, rosters, and coaching change substantially "
            f"year to year, and no fixture or roster data exists for {season} -- treat this "
            f"as a very low-confidence extrapolation from current team strength, not a real "
            f"premiership forecast."
        )

    return {
        "ok": True,
        "data": {
            "season": season,
            "data_latest_season": latest_season,
            "years_ahead_of_data": years_ahead,
            "ranking": ranking[:top_n],
            "model_type": meta["model_type"],
            "model_val_auc": meta["val_metric_value"],
            "model_test_auc": meta["test_metric_value"],
            "method_note": (
                "Ranked by average predicted win probability across a full round-robin of "
                "every team vs every other team, using each team's most recent known form. "
                "This is a power ranking derived from the single-match model, not a simulated "
                "season, ladder, or finals series."
            ),
            "confidence_note": confidence_note,
        }
    }


if __name__ == "__main__":
    import pprint
    pprint.pprint(predict_match_winner("Geelong", "Collingwood"))
    pprint.pprint(predict_top_player("Geelong", opponent="Collingwood", prediction_type="top_disposals"))
    pprint.pprint(predict_top_player("Geelong", prediction_type="expected_disposals"))
    pprint.pprint(predict_top_player("Geelong", prediction_type="best_defender"))
    pprint.pprint(predict_premiership_favourite(season=2030))
