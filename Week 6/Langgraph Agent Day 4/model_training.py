"""
model_training.py
==================
Section 8: Model training and chronological evaluation.

Trains:
  - Match winner classifier on afl_match_prediction_features.csv
  - Player "top disposals in match (for their team)" classifier on
    afl_player_prediction_features.csv
  - Player disposals regressor (expected disposals) on the same file

All targets and feature sets are taken from the manifest-confirmed,
leakage_safe / recommended_for_model columns (data_layer.get_manifest_safe_features),
with a manually-audited fallback list if the manifest is ever unavailable.

Splitting is strictly chronological by `season`:
    train:      season <= TRAIN_END
    validation: season == VAL_SEASON
    test:       season in TEST_SEASONS
No shuffling. No random_state-based row splitting. This mirrors what a
sportsbook would actually have known at each point in time.
"""
from __future__ import annotations
import json
import os
import numpy as np
import pandas as pd
from dataclasses import dataclass, asdict
from typing import Optional

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, roc_auc_score, log_loss, mean_absolute_error
import joblib

from data_layer import get_dataset, get_manifest_safe_features

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# Auto-detect an "artifacts" subfolder next to this file -- same
# zero-config rationale as data_layer.py's DATA_DIR.
MODEL_DIR = os.environ.get("AFL_MODEL_DIR", os.path.join(_THIS_DIR, "artifacts"))
os.makedirs(MODEL_DIR, exist_ok=True)

# Chronological split -- determined dynamically from the actual seasons present.
def _determine_split(seasons: pd.Series):
    years = sorted(seasons.unique())
    test_seasons = years[-2:]      # most recent 2 seasons -> test
    val_season = years[-3]         # season immediately before that -> validation
    train_end = years[-4]          # everything up to and including this -> train
    return train_end, val_season, test_seasons


# Manually-audited fallback exclusion lists (used only if manifest missing).
_MATCH_NON_FEATURE_COLS = {
    "match_id", "season", "round", "match_date", "venue", "home_team", "away_team", "home_win"
}
_PLAYER_NON_FEATURE_COLS = {
    "id", "team", "year", "opponent", "round", "result", "jersey_num", "match_date",
    "match_id", "season", "player_id",
    # raw same-match stats that would leak the outcome we're predicting:
    "kicks", "marks", "handballs", "disposals", "goals", "behinds", "hit_outs", "tackles",
    "rebound_50s", "inside_50s", "clearances", "clangers", "free_kicks_for",
    "free_kicks_against", "brownlow_votes", "contested_possessions",
    "uncontested_possessions", "contested_marks", "marks_inside_50", "one_percenters",
    "bounces", "goal_assist", "percentage_of_game_played", "fantasy_points", "margin",
    "player_score", "disposals_raw", "is_match_top_disposals", "is_match_top_goals",
    "career_game_count",
}


@dataclass
class ModelMetadata:
    task: str
    model_type: str
    feature_columns: list
    target: str
    train_seasons: str
    val_season: int
    test_seasons: list
    val_metric_name: str
    val_metric_value: float
    test_metric_name: str
    test_metric_value: float
    notes: str = ""


def _match_feature_columns() -> list:
    manifest_cols = get_manifest_safe_features("afl_match_prediction_features")
    mf = get_dataset("match_features")
    if manifest_cols:
        cols = [c for c in manifest_cols if c in mf.columns]
    else:
        cols = [c for c in mf.columns if c not in _MATCH_NON_FEATURE_COLS]
    return cols


def train_match_winner_model() -> ModelMetadata:
    """
    Train + chronologically evaluate a match-winner classifier.
    Baseline: Logistic Regression. Stronger model: Random Forest.
    Best model chosen by validation ROC-AUC, confirmed on the held-out test seasons.
    """
    mf = get_dataset("match_features").copy()
    mf = mf.dropna(subset=["home_win"])  # drop draws -- undefined binary target
    feature_cols = _match_feature_columns()

    train_end, val_season, test_seasons = _determine_split(mf["season"])
    train = mf[mf["season"] <= train_end]
    val = mf[mf["season"] == val_season]
    test = mf[mf["season"].isin(test_seasons)]

    X_train, y_train = train[feature_cols], train["home_win"].astype(int)
    X_val, y_val = val[feature_cols], val["home_win"].astype(int)
    X_test, y_test = test[feature_cols], test["home_win"].astype(int)

    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    X_train_i = imputer.fit_transform(X_train)
    X_val_i = imputer.transform(X_val)
    X_test_i = imputer.transform(X_test)
    X_train_s = scaler.fit_transform(X_train_i)
    X_val_s = scaler.transform(X_val_i)
    X_test_s = scaler.transform(X_test_i)

    candidates = {}

    logreg = LogisticRegression(max_iter=2000, C=1.0)
    logreg.fit(X_train_s, y_train)
    candidates["logistic_regression"] = (logreg, X_val_s, X_test_s, True)

    rf = RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=10,
                                 random_state=42, n_jobs=-1)
    rf.fit(X_train_i, y_train)  # tree model: raw imputed features, no scaling needed
    candidates["random_forest"] = (rf, X_val_i, X_test_i, False)

    results = {}
    for name, (model, Xv, Xt, _scaled) in candidates.items():
        val_proba = model.predict_proba(Xv)[:, 1]
        val_auc = roc_auc_score(y_val, val_proba)
        val_acc = accuracy_score(y_val, (val_proba >= 0.5).astype(int))
        results[name] = {"val_auc": val_auc, "val_acc": val_acc, "model": model, "Xt": Xt}
        print(f"[match model] {name}: val_auc={val_auc:.4f} val_acc={val_acc:.4f}")

    best_name = max(results, key=lambda n: results[n]["val_auc"])
    best = results[best_name]
    test_proba = best["model"].predict_proba(best["Xt"])[:, 1]
    test_auc = roc_auc_score(y_test, test_proba)
    test_acc = accuracy_score(y_test, (test_proba >= 0.5).astype(int))
    print(f"[match model] SELECTED {best_name}: test_auc={test_auc:.4f} test_acc={test_acc:.4f}")

    joblib.dump(best["model"], os.path.join(MODEL_DIR, "match_winner_model.joblib"))
    joblib.dump(imputer, os.path.join(MODEL_DIR, "match_winner_imputer.joblib"))
    if best_name == "logistic_regression":
        joblib.dump(scaler, os.path.join(MODEL_DIR, "match_winner_scaler.joblib"))
    else:
        joblib.dump(None, os.path.join(MODEL_DIR, "match_winner_scaler.joblib"))

    meta = ModelMetadata(
        task="match_winner", model_type=best_name, feature_columns=feature_cols,
        target="home_win", train_seasons=f"<= {train_end}", val_season=int(val_season),
        test_seasons=[int(s) for s in test_seasons],
        val_metric_name="roc_auc", val_metric_value=float(best["val_auc"]),
        test_metric_name="roc_auc", test_metric_value=float(test_auc),
        notes=f"val_acc={best['val_acc']:.4f}, test_acc={test_acc:.4f}. "
              f"Draws dropped from target (undefined for binary classification).",
    )
    with open(os.path.join(MODEL_DIR, "match_winner_metadata.json"), "w") as f:
        json.dump(asdict(meta), f, indent=2)
    return meta


def _player_feature_columns() -> list:
    manifest_cols = get_manifest_safe_features("afl_player_prediction_features")
    pf = get_dataset("player_features")
    if manifest_cols:
        cols = [c for c in manifest_cols if c in pf.columns]
    else:
        cols = [c for c in pf.columns if c not in _PLAYER_NON_FEATURE_COLS]
    return cols


def train_top_disposals_model() -> ModelMetadata:
    """Classifier: will this player be their team's top disposal-getter this match?"""
    pf = get_dataset("player_features").copy()
    feature_cols = _player_feature_columns()

    train_end, val_season, test_seasons = _determine_split(pf["season"])
    train = pf[pf["season"] <= train_end]
    val = pf[pf["season"] == val_season]
    test = pf[pf["season"].isin(test_seasons)]

    X_train, y_train = train[feature_cols], train["is_match_top_disposals"].astype(int)
    X_val, y_val = val[feature_cols], val["is_match_top_disposals"].astype(int)
    X_test, y_test = test[feature_cols], test["is_match_top_disposals"].astype(int)

    imputer = SimpleImputer(strategy="median")
    X_train_i = imputer.fit_transform(X_train)
    X_val_i = imputer.transform(X_val)
    X_test_i = imputer.transform(X_test)

    rf = RandomForestClassifier(n_estimators=150, max_depth=9, min_samples_leaf=25,
                                 class_weight="balanced", random_state=42, n_jobs=4)
    rf.fit(X_train_i, y_train)
    val_proba = rf.predict_proba(X_val_i)[:, 1]
    val_auc = roc_auc_score(y_val, val_proba)
    test_proba = rf.predict_proba(X_test_i)[:, 1]
    test_auc = roc_auc_score(y_test, test_proba)
    print(f"[top disposals model] random_forest: val_auc={val_auc:.4f} test_auc={test_auc:.4f}")

    joblib.dump(rf, os.path.join(MODEL_DIR, "top_disposals_model.joblib"))
    joblib.dump(imputer, os.path.join(MODEL_DIR, "top_disposals_imputer.joblib"))

    meta = ModelMetadata(
        task="player_top_disposals", model_type="random_forest", feature_columns=feature_cols,
        target="is_match_top_disposals", train_seasons=f"<= {train_end}",
        val_season=int(val_season), test_seasons=[int(s) for s in test_seasons],
        val_metric_name="roc_auc", val_metric_value=float(val_auc),
        test_metric_name="roc_auc", test_metric_value=float(test_auc),
        notes="Binary target = top disposal-getter within player's own team for that match.",
    )
    with open(os.path.join(MODEL_DIR, "top_disposals_metadata.json"), "w") as f:
        json.dump(asdict(meta), f, indent=2)
    return meta


def train_expected_disposals_model() -> ModelMetadata:
    """Regressor: expected disposal count for a player in their next match."""
    pf = get_dataset("player_features").copy()
    feature_cols = _player_feature_columns()

    train_end, val_season, test_seasons = _determine_split(pf["season"])
    train = pf[pf["season"] <= train_end]
    val = pf[pf["season"] == val_season]
    test = pf[pf["season"].isin(test_seasons)]

    X_train, y_train = train[feature_cols], train["disposals"]
    X_val, y_val = val[feature_cols], val["disposals"]
    X_test, y_test = test[feature_cols], test["disposals"]

    imputer = SimpleImputer(strategy="median")
    X_train_i = imputer.fit_transform(X_train)
    X_val_i = imputer.transform(X_val)
    X_test_i = imputer.transform(X_test)

    hgb = HistGradientBoostingRegressor(max_iter=200, max_depth=6, learning_rate=0.08,
                                         random_state=42)
    hgb.fit(X_train_i, y_train)
    val_mae = mean_absolute_error(y_val, hgb.predict(X_val_i))
    test_mae = mean_absolute_error(y_test, hgb.predict(X_test_i))
    print(f"[expected disposals model] hist_gradient_boosting: val_mae={val_mae:.3f} test_mae={test_mae:.3f}")

    joblib.dump(hgb, os.path.join(MODEL_DIR, "expected_disposals_model.joblib"))
    joblib.dump(imputer, os.path.join(MODEL_DIR, "expected_disposals_imputer.joblib"))

    meta = ModelMetadata(
        task="player_expected_disposals", model_type="hist_gradient_boosting", feature_columns=feature_cols,
        target="disposals", train_seasons=f"<= {train_end}",
        val_season=int(val_season), test_seasons=[int(s) for s in test_seasons],
        val_metric_name="mae", val_metric_value=float(val_mae),
        test_metric_name="mae", test_metric_value=float(test_mae),
        notes="Regression output is a predicted disposal COUNT, not a probability.",
    )
    with open(os.path.join(MODEL_DIR, "expected_disposals_metadata.json"), "w") as f:
        json.dump(asdict(meta), f, indent=2)
    return meta


if __name__ == "__main__":
    m1 = train_match_winner_model()
    print(m1)
    m2 = train_top_disposals_model()
    print(m2)
    m3 = train_expected_disposals_model()
    print(m3)
