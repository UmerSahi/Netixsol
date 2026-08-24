"""
retrieval_tools.py
===================
Section 7: Structured retrieval tools. Every function here queries the
cached CSVs directly with vectorized pandas -- never row-by-row loops, never
LLM memory. Every function returns a plain dict with either a "data" key
(success) or an "error"/"clarification" key (never both silently merged).

SUPPORTED_STATS below is the single source of truth for which per-match
stat names get_top_player_in_match / get_player_single_game_high will
accept -- this prevents arbitrary, unsupported stat names being silently
accepted.

CAPSTONE ADDITIONS (v2) -- these close three of the "genuine capability
gaps" flagged in evaluation:
    - get_player_multi_season_stats(): combined totals/averages across an
      explicit list of seasons (previously unsupported -- the agent used
      to either silently answer for one season or ask a confusing
      "give me a round" clarification for what was actually a multi-year
      request).
    - get_player_single_game_high(): the actual single-match maximum for a
      stat (optionally within one season), instead of silently
      substituting the season average when asked for a "highest game".
    - compare_players(): a genuine side-by-side two-player comparison
      tool, instead of silently answering for only the first player named.
"""
from __future__ import annotations
from typing import Optional
import pandas as pd
import numpy as np

from data_layer import get_dataset
from resolvers import resolve_team, resolve_player

SUPPORTED_STATS = {
    "disposals": "disposals",
    "disposal": "disposals",
    "kicks": "kicks",
    "kick": "kicks",
    "handballs": "handballs",
    "handball": "handballs",
    "marks": "marks",
    "goals": "goals",
    "goal": "goals",
    "goalkicking": "goals",
    "goalkicker": "goals",
    "behinds": "behinds",
    "tackles": "tackles",
    "tackle": "tackles",
    "hit_outs": "hit_outs",
    "hitouts": "hit_outs",
    "clearances": "clearances",
    "inside_50s": "inside_50s",
    "inside 50s": "inside_50s",
    "fantasy_points": "fantasy_points",
    "fantasy points": "fantasy_points",
    "contested_possessions": "contested_possessions",
    "contested possessions": "contested_possessions",
    "uncontested_possessions": "uncontested_possessions",
    "uncontested possessions": "uncontested_possessions",
    "contested_marks": "contested_marks",
    "contested marks": "contested_marks",
    "one_percenters": "one_percenters",
    "rebound_50s": "rebound_50s",
    "rebound 50s": "rebound_50s",
    "clangers": "clangers",
    "bounces": "bounces",
    "goal_assist": "goal_assist",
    "goal assists": "goal_assist",
}

# Stat columns considered safe/meaningful to sum & average for season and
# multi-season aggregation (a smaller, curated subset of SUPPORTED_STATS --
# things like fantasy_points are meaningful to sum too, so it's included).
_AGGREGATE_STAT_COLS = ["disposals", "kicks", "handballs", "marks", "goals", "behinds",
                         "tackles", "hit_outs", "clearances", "inside_50s", "fantasy_points",
                         "contested_possessions", "uncontested_possessions", "contested_marks",
                         "one_percenters", "rebound_50s", "clangers", "bounces", "goal_assist"]

_SUPPORTED_AGGREGATE_COLS = [
    column for column in dict.fromkeys(SUPPORTED_STATS.values())
    if column in _AGGREGATE_STAT_COLS
]


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _clarify(msg: str, candidates=None) -> dict:
    return {"ok": False, "clarification": msg, "candidates": candidates or []}


def _ok(data) -> dict:
    return {"ok": True, "data": data}


# ---------------------------------------------------------------------------
# 1. get_best_team_win_rate
# ---------------------------------------------------------------------------
def get_best_team_win_rate(years: list) -> dict:
    """Find the team with the highest win rate across the requested seasons."""
    seasons = sorted({int(year) for year in years})
    mr = get_dataset("match_retrieval")
    matches = mr[mr["season"].isin(seasons)]
    if matches.empty:
        return _err(f"No match data found for the {', '.join(map(str, seasons))} seasons.")

    home = matches[["season", "home_team", "winner_team", "result"]].rename(
        columns={"home_team": "team"}
    )
    away = matches[["season", "away_team", "winner_team", "result"]].rename(
        columns={"away_team": "team"}
    )
    team_matches = pd.concat([home, away], ignore_index=True)
    team_matches["win"] = (team_matches["winner_team"] == team_matches["team"]).astype(int)
    team_matches["draw"] = (team_matches["result"] == "D").astype(int)
    summary = team_matches.groupby("team", as_index=False).agg(
        games=("team", "size"), wins=("win", "sum"), draws=("draw", "sum")
    )
    summary["win_rate"] = (summary["wins"] + 0.5 * summary["draws"]) / summary["games"]
    summary = summary.sort_values(["win_rate", "wins", "team"], ascending=[False, False, True])
    best = summary.iloc[0]
    return _ok({
        "seasons": seasons,
        "team": best["team"],
        "games": int(best["games"]),
        "wins": int(best["wins"]),
        "draws": int(best["draws"]),
        "losses": int(best["games"] - best["wins"] - best["draws"]),
        "win_rate": round(float(best["win_rate"]), 4),
    })


# ---------------------------------------------------------------------------
# 2. get_team_match_in_round
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
def get_team_head_to_head(team: str, opponent: str, venue_scope: str = None) -> dict:
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
    if venue_scope == "home":
        rows = rows[rows["home_team"] == t1]
    elif venue_scope == "away":
        rows = rows[rows["away_team"] == t1]
    if rows.empty:
        scope_text = f" for {venue_scope} matches" if venue_scope else ""
        return _err(f"No historical matches found between {t1} and {t2}{scope_text} in the dataset.")

    wins_t1 = (rows["winner_team"] == t1).sum()
    wins_t2 = (rows["winner_team"] == t2).sum()
    draws = (rows["result"] == "D").sum()
    return _ok({
        "team": t1, "opponent": t2,
        "venue_scope": venue_scope,
        "matches_played": int(len(rows)),
        f"{t1}_wins": int(wins_t1), f"{t2}_wins": int(wins_t2), "draws": int(draws),
        f"{t1}_win_rate": round(float(wins_t1) / len(rows), 4),
        f"{t2}_win_rate": round(float(wins_t2) / len(rows), 4),
        "first_meeting": rows["match_date"].min(), "last_meeting": rows["match_date"].max(),
    })


def get_team_overview(team: str, year: int = None) -> dict:
    """Summarize a team's season using the available match dataset."""
    tr = resolve_team(team)
    if tr.status == "ambiguous":
        return _clarify(tr.message, tr.candidates)
    if tr.status == "not_found":
        return _err(tr.message)
    team_name = tr.value
    mr = get_dataset("match_retrieval")
    if year is None:
        year = int(mr["season"].max())
    rows = mr[(mr["season"] == int(year)) & ((mr["home_team"] == team_name) | (mr["away_team"] == team_name))]
    if rows.empty:
        return _err(f"No {year} season data found for {team_name}.")
    scores_for = rows.apply(lambda row: row["home_score"] if row["home_team"] == team_name else row["away_score"], axis=1)
    scores_against = rows.apply(lambda row: row["away_score"] if row["home_team"] == team_name else row["home_score"], axis=1)
    wins = int((rows["winner_team"] == team_name).sum())
    draws = int((rows["result"] == "D").sum())
    return _ok({"team": team_name, "year": int(year), "games": int(len(rows)), "wins": wins,
                "losses": int(len(rows) - wins - draws), "draws": draws,
                "win_rate": round((wins + 0.5 * draws) / len(rows), 4),
                "avg_score": round(float(scores_for.mean()), 2),
                "avg_conceded": round(float(scores_against.mean()), 2),
                "avg_margin": round(float((scores_for - scores_against).mean()), 2)})


def get_top_goal_scorers(year: int, limit: int = 5) -> dict:
    """Return the players with the most goals in a season."""
    pr = get_dataset("player_retrieval")
    rows = pr[pr["year"] == int(year)]
    if rows.empty:
        return _err(f"No {year} season data found in the dataset.")
    ranking = (rows.groupby("player_name", as_index=False)
               .agg(goals=("goals", "sum"), games=("goals", "size"))
               .sort_values(["goals", "player_name"], ascending=[False, True])
               .head(int(limit)))
    return _ok({"year": int(year), "scorers": [
        {"rank": index + 1, "player": row["player_name"], "goals": int(row["goals"]), "games": int(row["games"])}
        for index, (_, row) in enumerate(ranking.iterrows())
    ]})


def compare_team_head_to_heads(reference_team: str, first_opponent: str,
                               second_opponent: str, year: int) -> dict:
    """Compare one team's wins against two named opponents in one season."""
    comparisons = []
    for opponent in (first_opponent, second_opponent):
        result = get_team_head_to_head(reference_team, opponent)
        if not result.get("ok"):
            return result
        data = result["data"]
        matches = get_dataset("match_retrieval")
        matches = matches[(matches["season"] == int(year)) & (
            ((matches["home_team"] == data["team"]) & (matches["away_team"] == data["opponent"])) |
            ((matches["home_team"] == data["opponent"]) & (matches["away_team"] == data["team"])))].copy()
        comparisons.append({
            "opponent": data["opponent"],
            "matches": int(len(matches)),
            "reference_wins": int((matches["winner_team"] == data["team"]).sum()),
            "opponent_wins": int((matches["winner_team"] == data["opponent"]).sum()),
            "draws": int((matches["result"] == "D").sum()),
        })
    return _ok({"team": data["team"], "year": int(year), "comparisons": comparisons})


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
    stat_cols = [c for c in _SUPPORTED_AGGREGATE_COLS if c in rows.columns]
    totals = {f"total_{c}": float(rows[c].sum(skipna=True)) for c in stat_cols}
    averages = {f"avg_{c}": round(float(rows[c].mean(skipna=True)), 2) for c in stat_cols}
    return _ok({
        "player": name, "year": int(year), "games_played": int(len(rows)),
        "team": rows["team"].iloc[-1],
        **totals, **averages,
    })


def get_team_recent_stats(team: str, games: int = 10, before_year: int = None) -> dict:
    """Return the team's most recent completed matches and form summary."""
    tr = resolve_team(team)
    if tr.status == "ambiguous":
        return _clarify(tr.message, tr.candidates)
    if tr.status == "not_found":
        return _err(tr.message)
    team_name = tr.value
    if games < 1:
        return _err("The number of recent games must be positive.")

    mr = get_dataset("match_retrieval").copy()
    rows = mr[(mr["home_team"] == team_name) | (mr["away_team"] == team_name)].copy()
    if rows.empty:
        return _err(f"No match history found for {team_name} in the dataset.")
    rows["match_date"] = pd.to_datetime(rows["match_date"], errors="coerce")
    if before_year is not None:
        cutoff_date = pd.Timestamp(f"{int(before_year)}-01-01")
        rows = rows[rows["match_date"] < cutoff_date]
        if rows.empty:
            return _err(f"No match history found for {team_name} before {before_year}.")
    rows["team_score"] = rows.apply(
        lambda row: row["home_score"] if row["home_team"] == team_name else row["away_score"], axis=1)
    rows["opponent_score"] = rows.apply(
        lambda row: row["away_score"] if row["home_team"] == team_name else row["home_score"], axis=1)
    rows["opponent"] = rows.apply(
        lambda row: row["away_team"] if row["home_team"] == team_name else row["home_team"], axis=1)
    rows["result_for_team"] = rows.apply(
        lambda row: "draw" if row["result"] == "D" else ("win" if row["winner_team"] == team_name else "loss"), axis=1)
    rows = rows.sort_values("match_date", ascending=False).head(int(games))
    result_counts = rows["result_for_team"].value_counts()
    matches = [{
        "season": int(row["season"]), "round": str(row["round"]),
        "date": row["match_date"].strftime("%Y-%m-%d"),
        "opponent": row["opponent"], "team_score": int(row["team_score"]),
        "opponent_score": int(row["opponent_score"]), "result": row["result_for_team"],
    } for _, row in rows.iterrows()]
    return _ok({
        "team": team_name, "games_requested": int(games), "before_year": before_year,
        "games_found": int(len(rows)),
        "wins": int(result_counts.get("win", 0)), "losses": int(result_counts.get("loss", 0)),
        "draws": int(result_counts.get("draw", 0)),
        "win_rate": round(float((rows["result_for_team"] == "win").mean()), 4),
        "avg_score": round(float(rows["team_score"].mean()), 2),
        "avg_conceded": round(float(rows["opponent_score"].mean()), 2),
        "avg_margin": round(float((rows["team_score"] - rows["opponent_score"]).mean()), 2),
        "matches": matches,
    })


# ---------------------------------------------------------------------------
# 4b. get_player_multi_season_stats  (NEW)
# ---------------------------------------------------------------------------
def get_player_multi_season_stats(player: str, years: list, stat: str = None) -> dict:
    """
    Combined totals/averages for a player across an EXPLICIT list of
    seasons (e.g. "tackles across 2022 and 2023 combined"). Distinct from
    get_player_season_stats (one season) -- this exists specifically
    because that gap previously produced a confusing "give me a round"
    clarification for a request that was never missing a round at all,
    just a multi-year aggregation the tool layer didn't support.

    `stat`, if given, is echoed back explicitly as stat_requested /
    total_requested_stat / avg_requested_stat so the formatter reports the
    SPECIFIC stat the person asked about (e.g. tackles) rather than
    defaulting to disposals regardless of what was requested.
    """
    if not years:
        return _err("I need at least one season/year to look up combined stats.")
    pres = resolve_player(player)
    if pres.status == "ambiguous":
        return _clarify(pres.message, pres.candidates)
    if pres.status == "not_found":
        return _err(pres.message)
    name = pres.value

    stat_col = None
    if stat is not None:
        stat_key = stat.strip().lower()
        if stat_key not in SUPPORTED_STATS:
            return _err(
                f"'{stat}' is not a supported statistic. Supported stats: "
                f"{', '.join(sorted(set(SUPPORTED_STATS.values())))}."
            )
        stat_col = SUPPORTED_STATS[stat_key]

    pr = get_dataset("player_retrieval")
    years_int = sorted({int(y) for y in years})
    rows = pr[(pr["player_name"] == name) & (pr["year"].isin(years_int))]
    if rows.empty:
        return _err(f"No data found for {name} in {', '.join(str(y) for y in years_int)}.")

    found_years = sorted(rows["year"].unique().tolist())
    missing_years = [y for y in years_int if y not in found_years]

    per_season = {}
    for y in found_years:
        yr = rows[rows["year"] == y]
        per_season[int(y)] = {
            "games_played": int(len(yr)),
            **{c: round(float(yr[c].sum(skipna=True)), 1) for c in _AGGREGATE_STAT_COLS if c in yr.columns},
        }

    totals = {f"total_{c}": round(float(rows[c].sum(skipna=True)), 1)
              for c in _AGGREGATE_STAT_COLS if c in rows.columns}
    averages = {f"avg_{c}": round(float(rows[c].mean(skipna=True)), 2)
                for c in _AGGREGATE_STAT_COLS if c in rows.columns}

    out = {
        "player": name, "seasons_requested": years_int, "seasons_found": found_years,
        "seasons_missing_data": missing_years,
        "games_played": int(len(rows)),
        "per_season": per_season,
        **totals, **averages,
    }
    if stat_col:
        out["stat_requested"] = stat_col
        out["total_requested_stat"] = totals.get(f"total_{stat_col}")
        out["avg_requested_stat"] = averages.get(f"avg_{stat_col}")
    return _ok(out)


# ---------------------------------------------------------------------------
# 4c. get_player_single_game_high  (NEW)
# ---------------------------------------------------------------------------
def get_player_single_game_high(player: str, stat: str, year: int = None) -> dict:
    """
    The player's actual single-match MAXIMUM for `stat`, optionally scoped
    to one season, otherwise across their whole career in the dataset.
    This intentionally returns a single-game peak, never a season average
    -- silently substituting an average for a "highest game" question was
    a flagged, concerning failure mode (it answers a different question
    than what was asked without saying so).
    """
    stat_key = stat.strip().lower()
    if stat_key not in SUPPORTED_STATS:
        return _err(
            f"'{stat}' is not a supported statistic. Supported stats: "
            f"{', '.join(sorted(set(SUPPORTED_STATS.values())))}."
        )
    stat_col = SUPPORTED_STATS[stat_key]

    pres = resolve_player(player)
    if pres.status == "ambiguous":
        return _clarify(pres.message, pres.candidates)
    if pres.status == "not_found":
        return _err(pres.message)
    name = pres.value

    pr = get_dataset("player_retrieval")
    rows = pr[pr["player_name"] == name]
    scope = "career"
    if year is not None:
        rows = rows[rows["year"] == int(year)]
        scope = str(int(year))
    rows = rows.dropna(subset=[stat_col])
    if rows.empty:
        return _err(f"No '{stat_col}' data found for {name}" + (f" in {year}." if year else "."))

    top = rows.loc[rows[stat_col].idxmax()]
    return _ok({
        "player": name, "stat": stat_col, "scope": scope,
        "value": float(top[stat_col]), "year": int(top["year"]), "round": str(top["round"]),
        "opponent": top["opponent"], "team": top["team"],
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


# ---------------------------------------------------------------------------
# 6. compare_players  (NEW)
# ---------------------------------------------------------------------------
def compare_players(player_a: str, player_b: str, year: int = None, stat: str = None) -> dict:
    """
    Genuine side-by-side comparison of two named players. If `year` is
    given, compares that season; otherwise compares each player's most
    recent season present in the data (their latest known form). If `stat`
    is given, the comparison highlights that one stat; otherwise a full
    stat line is returned for both players.

    This is a dedicated tool specifically so a two-player question never
    silently collapses into a single-player answer.
    """
    pres_a, pres_b = resolve_player(player_a), resolve_player(player_b)
    for pres, label in [(pres_a, player_a), (pres_b, player_b)]:
        if pres.status == "ambiguous":
            return _clarify(pres.message, pres.candidates)
        if pres.status == "not_found":
            return _err(pres.message)
    name_a, name_b = pres_a.value, pres_b.value
    if name_a == name_b:
        return _err("A comparison requires two different players.")

    if stat is not None:
        stat_key = stat.strip().lower()
        if stat_key not in SUPPORTED_STATS:
            return _err(
                f"'{stat}' is not a supported statistic. Supported stats: "
                f"{', '.join(sorted(set(SUPPORTED_STATS.values())))}."
            )
        stat_col = SUPPORTED_STATS[stat_key]
    else:
        stat_col = None

    pr = get_dataset("player_retrieval")

    def _season_row(name):
        rows = pr[pr["player_name"] == name]
        if rows.empty:
            return None, None
        yr = int(year) if year is not None else int(rows["year"].max())
        season_rows = rows[rows["year"] == yr]
        if season_rows.empty:
            return yr, None
        return yr, season_rows

    yr_a, rows_a = _season_row(name_a)
    yr_b, rows_b = _season_row(name_b)
    if rows_a is None:
        return _err(f"No data found for {name_a}.")
    if rows_b is None:
        return _err(f"No data found for {name_b}.")
    if rows_a is None or len(rows_a) == 0:
        return _err(f"No {yr_a} season data found for {name_a}.")
    if rows_b is None or len(rows_b) == 0:
        return _err(f"No {yr_b} season data found for {name_b}.")

    def _line(rows, stat_cols):
        return {f"avg_{c}": round(float(rows[c].mean(skipna=True)), 2) for c in stat_cols}

    cols = [stat_col] if stat_col else ["disposals", "kicks", "handballs", "marks", "goals",
                                         "tackles", "clearances", "inside_50s", "fantasy_points"]
    line_a = _line(rows_a, cols)
    line_b = _line(rows_b, cols)

    data = {
        "player_a": {"name": name_a, "season": yr_a, "games_played": int(len(rows_a)), **line_a},
        "player_b": {"name": name_b, "season": yr_b, "games_played": int(len(rows_b)), **line_b},
    }
    if stat_col:
        val_a, val_b = line_a[f"avg_{stat_col}"], line_b[f"avg_{stat_col}"]
        data["stat_compared"] = stat_col
        data["leader"] = name_a if val_a > val_b else (name_b if val_b > val_a else "tied")
    return _ok(data)


if __name__ == "__main__":
    print(get_team_match_in_round("Geelong", 2020, 5))
    print(get_team_head_to_head("Cats", "Pies"))
    print(get_player_match_stats("Patrick Dangerfield", 2020, 5))
    print(get_player_season_stats("Patrick Dangerfield", 2020))
    print(get_player_multi_season_stats("Patrick Dangerfield", [2020, 2021]))
    print(get_player_single_game_high("Patrick Dangerfield", "disposals", 2020))
    print(get_top_player_in_match("Geelong", 2020, 5, "disposals"))
    print(get_top_player_in_match("Geelong", 2020, 5, "not_a_stat"))
    print(get_team_match_in_round("Giants", 1990, 5))  # GWS didn't exist yet -> should error cleanly
