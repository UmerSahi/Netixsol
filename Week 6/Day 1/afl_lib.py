"""
afl_lib.py
==========
Reusable, leakage-safe data-cleaning and feature-engineering library for the
AFL dataset. Every function here is imported and called from the companion
notebook `AFL_Data_Foundations.ipynb` — nothing in the notebook duplicates
this logic, so there is exactly one source of truth for every transformation.

Sections
--------
1. Team-name canonicalisation         (import from clean_teams)
2. Raw table loaders + cleaners       (load_*)
3. Match-level table construction     (build_match_table)
4. Player-level table construction    (build_player_game_table)
5. Position proxy (heuristic cluster) (assign_position_proxy)
6. Prediction-target definitions      (add_match_targets, add_player_targets)
7. Leak-free rolling feature builders (add_team_form_features, add_h2h_features,
                                        add_ladder_features, add_player_form_features)
8. Time-based train/hold-out split    (time_based_split)
"""
from __future__ import annotations
import re
import numpy as np
import pandas as pd

from clean_teams import canonical_series, canonical_team, CLUB_HISTORY_NOTES

RAW_DIR = "."

# ---------------------------------------------------------------------------
# 2. Raw loaders
# ---------------------------------------------------------------------------

def load_raw_tables(raw_dir: str = RAW_DIR) -> dict[str, pd.DataFrame]:
    """Load all 4 raw CSVs untouched (dtypes as pandas infers them)."""
    info = pd.read_csv(f"{raw_dir}/afl_players_info_raw.csv")
    season = pd.read_csv(f"{raw_dir}/afl_players_seasonal_stats_raw.csv", low_memory=False)
    rbr = pd.read_csv(f"{raw_dir}/round_by_round_stats.csv", low_memory=False)
    tm = pd.read_csv(f"{raw_dir}/team_matches_home_away_raw.csv")
    return {"info": info, "season": season, "rbr": rbr, "team_matches": tm}


def _clean_venue(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.replace("\t", " ", regex=False)
        .str.replace("\n", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def clean_player_info(info: pd.DataFrame) -> pd.DataFrame:
    """De-duplicate exact-duplicate player rows and parse date columns."""
    out = info.drop_duplicates(subset=["id"]).copy()
    for c in ["born_date", "debut_date", "last_date"]:
        out[c] = pd.to_datetime(out[c], errors="coerce")
    out = out.rename(columns={"id": "player_id"})
    return out


def clean_season_table(season: pd.DataFrame) -> pd.DataFrame:
    out = season.copy()
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("Int64")
    out["team"] = canonical_series(out["team"])
    out = out.drop_duplicates(subset=["player_id", "year", "team", "is_finals"])
    return out


def clean_round_by_round(rbr: pd.DataFrame) -> pd.DataFrame:
    out = rbr.copy()
    out["team"] = canonical_series(out["team"])
    out["opponent"] = canonical_series(out["opponent"])
    out["match_date"] = pd.to_datetime(out["match_date"], errors="coerce")
    out["player_id"] = pd.to_numeric(out["player_id"], errors="coerce").astype("Int64")

    # --- documented data-quality fixes ---
    # (a) 'score' column is 0 for every one of 217,568 rows -> unusable, drop it
    #     and derive a real player-game score from goals/behinds instead.
    out = out.drop(columns=["score"])
    out["player_score"] = out["goals"] * 6 + out["behinds"]

    # (b) 'disposals' disagrees with kicks+handballs for ~9.7% of rows, and is
    #     negative (impossible) for 720 rows. kicks/handballs are internally
    #     consistent, so disposals is recomputed from them as the trustworthy
    #     version; the raw column is kept as disposals_raw for audit purposes.
    out["disposals_raw"] = out["disposals"]
    out["disposals"] = out["kicks"] + out["handballs"]

    # (c) one known match (Hawthorn v North Melbourne, 1994-09-10) has a
    #     scraping error in margin/result for a handful of player rows —
    #     these get corrected downstream once merged onto the authoritative
    #     match table in build_match_table(); no fix needed here.
    return out


# ---------------------------------------------------------------------------
# 3. Match-level table
# ---------------------------------------------------------------------------

def build_match_table(tm: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse the 2-rows-per-match (home_away in {H, A}) team_matches_home_away_raw
    table into ONE row per match — the grain a "match winner" model needs.

    Verified 1:1 structure: exactly 7,904 'H' rows and 7,904 'A' rows, unique on
    (match_date, canonical home team).
    """
    out = tm.copy()
    out["team_name"] = canonical_series(out["team_name"])
    out["opponent"] = canonical_series(out["opponent"])
    out["venue"] = _clean_venue(out["venue"])
    out["match_date"] = pd.to_datetime(out["match_date"], errors="coerce")

    home = out[out["home_away"] == "H"].copy()
    away = out[out["home_away"] == "A"].copy()

    m = home.merge(
        away[["match_date", "team_name", "team_score", "opponent_score", "crowd"]],
        left_on=["match_date", "opponent"],
        right_on=["match_date", "team_name"],
        suffixes=("", "_away_row"),
        how="left",
        validate="one_to_one",
    )

    match = pd.DataFrame({
        "match_id": home["match_date"].dt.strftime("%Y%m%d") + "_" + home["team_name"].str.replace(" ", ""),
        "season": home["year"],
        "round": home["round"],
        "match_date": home["match_date"],
        "venue": home["venue"],
        "home_team": home["team_name"],
        "away_team": home["opponent"],
        "home_score": home["team_score"],
        "away_score": home["opponent_score"],
        "crowd": home["crowd"],
    })

    # (fix) recompute margin/result from scores directly (authoritative,
    # fixes the single known scraping anomaly on 1994-09-10 QF)
    match["margin"] = match["home_score"] - match["away_score"]
    match["result"] = np.select(
        [match["margin"] > 0, match["margin"] < 0],
        ["H", "A"],
        default="D",
    )
    match = match.sort_values("match_date").reset_index(drop=True)
    return match


# ---------------------------------------------------------------------------
# 4. Player-game table
# ---------------------------------------------------------------------------

def build_player_game_table(rbr_clean: pd.DataFrame, match_table: pd.DataFrame) -> pd.DataFrame:
    """Attach match_id + home/away flag to every player-game row so player
    features can be joined back onto the match grain without ambiguity."""
    home_key = match_table[["match_id", "match_date", "season", "home_team"]].rename(columns={"home_team": "team"})
    away_key = match_table[["match_id", "match_date", "season", "away_team"]].rename(columns={"away_team": "team"})
    key = pd.concat([
        home_key.assign(is_home=True),
        away_key.assign(is_home=False),
    ])
    out = rbr_clean.merge(key, on=["match_date", "team"], how="left", validate="many_to_one")
    n_unmatched = out["match_id"].isna().sum()
    if n_unmatched:
        out.attrs["n_unmatched_player_rows"] = int(n_unmatched)
    return out


# ---------------------------------------------------------------------------
# 5. Position proxy
# ---------------------------------------------------------------------------

POSITION_PROXY_FEATURES = [
    "avg_hit_outs", "avg_inside_50s", "avg_rebound_50s", "avg_clearances",
    "avg_marks_inside_50", "avg_contested_possessions", "avg_goals", "avg_tackles",
]


def assign_position_proxy(season_clean: pd.DataFrame, n_clusters: int = 4, random_state: int = 42):
    """
    NOTE: this dataset has NO ground-truth position field. This function
    derives a heuristic position label per player (career-average stat
    profile -> KMeans cluster -> label by inspecting cluster centres) purely
    so Task 3's "position-based differences" can be explored. It is an
    approximation, not verified against real player positions, and every
    downstream use of it must say so.
    """
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler

    prof = (
        season_clean.groupby("player_id")[POSITION_PROXY_FEATURES]
        .mean()
        .dropna(thresh=len(POSITION_PROXY_FEATURES) - 1)
        .fillna(0)
    )
    X = StandardScaler().fit_transform(prof.values)
    km = KMeans(n_clusters=n_clusters, random_state=random_state, n_init=10).fit(X)
    prof = prof.copy()
    prof["cluster"] = km.labels_

    # Score every (cluster, candidate-label) pair, then solve a 1-1 assignment
    # (Hungarian algorithm) so each of the 4 clusters gets the label it best
    # matches *relative to the other clusters* -- far more robust than
    # checking single-feature maxima one label at a time.
    from scipy.optimize import linear_sum_assignment

    centers = pd.DataFrame(km.cluster_centers_, columns=POSITION_PROXY_FEATURES)
    label_scores = pd.DataFrame({
        "Ruck (proxy)": centers["avg_hit_outs"],
        "Defender (proxy)": centers["avg_rebound_50s"] - centers["avg_goals"] - centers["avg_inside_50s"],
        "Forward (proxy)": centers["avg_goals"] + centers["avg_marks_inside_50"] + centers["avg_inside_50s"],
        "Midfielder (proxy)": centers["avg_clearances"] + centers["avg_contested_possessions"] + centers["avg_tackles"],
    })
    row_ind, col_ind = linear_sum_assignment(-label_scores.values)  # maximise total score
    label_map = {r: label_scores.columns[c] for r, c in zip(row_ind, col_ind)}

    prof["position_proxy"] = prof["cluster"].map(label_map)
    return prof[["position_proxy"]].reset_index(), centers.assign(label=centers.index.map(label_map))


# ---------------------------------------------------------------------------
# 6. Prediction-target definitions
# ---------------------------------------------------------------------------

def add_match_targets(match: pd.DataFrame) -> pd.DataFrame:
    """
    Target A (classification): result_3class in {H, A, D} — home win / away
                                win / draw, from the home team's perspective.
    Target B (regression):     margin = home_score - away_score (signed).
    Target C (binary, for simple win-probability models; drops draws, ~0.8%
                                of matches): home_win in {0, 1}.
    """
    out = match.copy()
    out["home_win"] = np.where(out["result"] == "H", 1, np.where(out["result"] == "A", 0, np.nan))
    return out


FANTASY_NOTE = (
    "fantasy_points is provided pre-computed in the source round_by_round table "
    "(kept as-is; exact scoring weights are not documented by the source, so we "
    "treat it as an opaque but internally consistent composite rather than "
    "re-deriving it)."
)


def add_player_targets(player_game: pd.DataFrame) -> pd.DataFrame:
    """
    Adds per-game 'top player' indicator targets at the MATCH level (was this
    player the leading disposal-getter / goal-kicker in this specific match).
    Season-level leaderboards (total & per-game-average) are built separately
    in the EDA section directly from season_clean, since they are aggregates,
    not per-row targets.
    """
    out = player_game.copy()
    out["is_match_top_disposals"] = (
        out.groupby("match_id")["disposals"].transform("max") == out["disposals"]
    ).astype(int)
    out["is_match_top_goals"] = (
        out.groupby("match_id")["goals"].transform("max") == out["goals"]
    ).astype(int)
    return out


# ---------------------------------------------------------------------------
# 7. Leak-free rolling features
# ---------------------------------------------------------------------------

def _team_long_format(match: pd.DataFrame) -> pd.DataFrame:
    """Reshape match table (1 row/match) into 2 rows/match (1 per team),
    which is the natural shape for computing each team's rolling form."""
    home = pd.DataFrame({
        "match_id": match["match_id"], "match_date": match["match_date"], "season": match["season"],
        "team": match["home_team"], "opponent": match["away_team"], "venue": match["venue"],
        "is_home": True, "team_score": match["home_score"], "opp_score": match["away_score"],
        "win": (match["result"] == "H").astype(int),
        "draw": (match["result"] == "D").astype(int),
    })
    away = pd.DataFrame({
        "match_id": match["match_id"], "match_date": match["match_date"], "season": match["season"],
        "team": match["away_team"], "opponent": match["home_team"], "venue": match["venue"],
        "is_home": False, "team_score": match["away_score"], "opp_score": match["home_score"],
        "win": (match["result"] == "A").astype(int),
        "draw": (match["result"] == "D").astype(int),
    })
    long = pd.concat([home, away], ignore_index=True).sort_values(["team", "match_date"])
    long["margin"] = long["team_score"] - long["opp_score"]
    long["points"] = np.select([long["win"] == 1, long["draw"] == 1], [4, 2], default=0)  # AFL: 4/2/0
    return long


def add_team_form_features(match: pd.DataFrame, windows=(3, 5, 10)) -> pd.DataFrame:
    """
    Rolling team-form features computed STRICTLY from games played BEFORE the
    match being featured: every rolling stat uses `.shift(1)` prior to the
    rolling window, so game t's features only see games < t. Also adds
    win/loss streak and days-of-rest.
    """
    long = _team_long_format(match)
    g = long.groupby("team", group_keys=False)

    for w in windows:
        long[f"form_win_rate_last{w}"] = g["win"].apply(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
        long[f"form_avg_score_for_last{w}"] = g["team_score"].apply(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
        long[f"form_avg_score_against_last{w}"] = g["opp_score"].apply(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
        long[f"form_avg_margin_last{w}"] = g["margin"].apply(lambda s: s.shift(1).rolling(w, min_periods=1).mean())

    # win/loss streak as of before this game (positive = winning streak, negative = losing streak)
    def _streak(s: pd.Series) -> pd.Series:
        prev = s.shift(1)
        streak = np.zeros(len(prev))
        cur = 0
        vals = prev.values
        for i, v in enumerate(vals):
            if pd.isna(v):
                cur = 0
            elif v == 1:
                cur = cur + 1 if cur >= 0 else 1
            else:
                cur = cur - 1 if cur <= 0 else -1
            streak[i] = cur
        return pd.Series(streak, index=s.index)

    long["team_streak_entering_game"] = g["win"].apply(_streak)

    # days of rest since the team's previous match
    long["days_since_last_match"] = g["match_date"].diff().dt.days
    # number of matches the team has already played this season (before this one)
    long["games_played_this_season"] = long.groupby(["team", "season"]).cumcount()
    # venue experience: how many prior times this team has played at this venue
    long["venue_experience"] = long.groupby(["team", "venue"]).cumcount()

    return long


def add_ladder_features(long_form: pd.DataFrame) -> pd.DataFrame:
    """Ladder position (1 = top) computed from competition points and
    percentage accumulated STRICTLY before the current match, within season."""
    lf = long_form.copy()
    lf["points_cum_prior"] = lf.groupby(["team", "season"])["points"].cumsum() - lf["points"]
    lf["scored_cum_prior"] = lf.groupby(["team", "season"])["team_score"].cumsum() - lf["team_score"]
    lf["conceded_cum_prior"] = lf.groupby(["team", "season"])["opp_score"].cumsum() - lf["opp_score"]
    lf["percentage_prior"] = np.where(
        lf["conceded_cum_prior"] > 0,
        100 * lf["scored_cum_prior"] / lf["conceded_cum_prior"],
        100.0,
    )

    def _rank_within_round(df: pd.DataFrame) -> pd.Series:
        return df[["points_cum_prior", "percentage_prior"]].apply(tuple, axis=1).rank(
            method="min", ascending=False
        )

    lf["ladder_position_prior"] = (
        lf.groupby(["season", "match_date"], group_keys=False)
        .apply(lambda d: pd.Series(
            (-d["points_cum_prior"] * 100000 - d["percentage_prior"]).rank(method="min"),
            index=d.index,
        ))
    )
    return lf


def add_h2h_features(long_form: pd.DataFrame) -> pd.DataFrame:
    """Head-to-head win rate for (team, opponent) using only PRIOR meetings
    between the same two clubs (all-time, not season-limited)."""
    lf = long_form.sort_values(["team", "opponent", "match_date"]).copy()
    g = lf.groupby(["team", "opponent"], group_keys=False)
    lf["h2h_win_rate_prior"] = g["win"].apply(lambda s: s.shift(1).expanding().mean())
    lf["h2h_games_played_prior"] = g["win"].cumcount()
    return lf.sort_values(["match_id", "team"])


def add_player_form_features(player_game: pd.DataFrame, windows=(3, 5, 10)) -> pd.DataFrame:
    """Leak-free rolling player output features: shift(1) before rolling, so
    a player's features for game t use only games < t."""
    out = player_game.sort_values(["player_id", "match_date"]).copy()
    g = out.groupby("player_id", group_keys=False)
    for w in windows:
        for stat in ["disposals", "goals", "fantasy_points", "tackles", "player_score"]:
            out[f"player_{stat}_avg_last{w}"] = g[stat].apply(
                lambda s, w=w: s.shift(1).rolling(w, min_periods=1).mean()
            )
    out["player_games_played_prior"] = g.cumcount()
    out["player_career_avg_disposals_prior"] = g["disposals"].apply(lambda s: s.shift(1).expanding().mean())
    return out.sort_values(["match_id", "team", "player_id"])


def assemble_match_feature_table(match_targets: pd.DataFrame, team_long: pd.DataFrame) -> pd.DataFrame:
    """Merge the home/away rolling-team-form rows (2 rows/match, from
    add_team_form_features -> add_ladder_features -> add_h2h_features) back
    onto the 1-row-per-match target table, producing the final versioned
    match-level feature table."""
    drop_cols = ["team", "opponent", "match_date", "season", "venue", "is_home",
                 "team_score", "opp_score", "win", "draw", "margin", "points"]
    home_feats = team_long[team_long["is_home"]].drop(columns=drop_cols).add_prefix("home_")
    away_feats = team_long[~team_long["is_home"]].drop(columns=drop_cols).add_prefix("away_")
    feat = match_targets.merge(home_feats, left_on="match_id", right_on="home_match_id", how="left")
    feat = feat.merge(away_feats, left_on="match_id", right_on="away_match_id", how="left")
    feat = feat.drop(columns=["home_match_id", "away_match_id"])
    return feat


# ---------------------------------------------------------------------------
# 8. Time-based train / hold-out split
# ---------------------------------------------------------------------------

def time_based_split(df: pd.DataFrame, date_col: str = "match_date",
                      holdout_seasons: int = 1, season_col: str = "season"):
    """
    Strict chronological split: the most recent `holdout_seasons` season(s)
    become the hold-out set; everything earlier is training data. Returns
    (train_df, holdout_df). This is the ONE function every model this week
    must call, so every model is evaluated on an identical, leakage-free
    split.

    A random row-level split would let a model trained on, e.g., round 14 of
    2023 "see" round 3 of 2023 team form indirectly (rolling features for
    round 3 partly depend on round 14 not existing yet -- but more importantly
    a model could be fit on rows from the SAME season as its test rows,
    letting it implicitly learn that season's final ladder outcomes, injury
    situations, and squad changes that a real deployment could never know in
    advance). Sports form and squad composition drift season to season, so
    the only honest test is: could this model have made this prediction
    using nothing but data from before that season started?
    """
    max_season = df[season_col].max()
    cutoff_seasons = set(range(max_season - holdout_seasons + 1, max_season + 1))
    holdout = df[df[season_col].isin(cutoff_seasons)].copy()
    train = df[~df[season_col].isin(cutoff_seasons)].copy()
    return train.sort_values(date_col), holdout.sort_values(date_col)
