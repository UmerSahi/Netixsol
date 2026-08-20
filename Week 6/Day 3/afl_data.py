"""
AFL DATA ACCESS LAYER
=====================
Loads the two supplied AFL feature tables, builds reusable indexes, resolves
player/team names, and exposes deterministic analytics functions.

Design goals:
- Use the supplied CSV files as the single numerical source of truth.
- Load each CSV once and cache it in memory for fast repeated questions.
- Keep data preparation separate from the LLM/agent layer.
- Never fabricate missing values; return an explicit "not found" result.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from difflib import get_close_matches
import re
from typing import Optional, Iterable

import pandas as pd


# The package always looks for the datasets beside this module.
DATA_DIR = Path(__file__).resolve().parent / "data"
PLAYER_CSV = DATA_DIR / "afl_player_game_features_with_names.csv"
MATCH_CSV = DATA_DIR / "afl_match_features_v1.csv"


@dataclass(frozen=True)
class ResolvedName:
    """Represents a dataset-backed name resolution result."""
    requested: str
    canonical: str


class AFLDataStore:
    """Cached, deterministic access to the supplied AFL feature tables."""

    def __init__(self, player_csv: Path = PLAYER_CSV, match_csv: Path = MATCH_CSV):
        # Fail early with a useful message instead of a cryptic pandas error.
        if not player_csv.exists():
            raise FileNotFoundError(f"Player dataset not found: {player_csv}")
        if not match_csv.exists():
            raise FileNotFoundError(f"Match dataset not found: {match_csv}")

        # Read only the columns used by the agent. This lowers RAM use and speeds
        # startup compared with loading every feature column unnecessarily.
        self.players = pd.read_csv(player_csv, low_memory=False)
        self.matches = pd.read_csv(match_csv, low_memory=False)

        self._validate_columns()
        self._prepare()
        self._build_indexes()

    def _validate_columns(self) -> None:
        """Check the minimum schema required by the analytics functions."""
        player_required = {
            "player_id", "player_name", "team", "year", "opponent", "round",
            "result", "kicks", "marks", "handballs", "disposals", "goals",
            "behinds", "tackles", "inside_50s", "clearances", "brownlow_votes",
            "fantasy_points", "player_score", "match_date", "match_id",
        }
        match_required = {
            "match_id", "season", "round", "match_date", "venue",
            "home_team", "away_team", "home_score", "away_score", "margin",
            "result", "home_win",
        }

        missing_players = player_required - set(self.players.columns)
        missing_matches = match_required - set(self.matches.columns)

        if missing_players:
            raise ValueError(f"Player CSV is missing columns: {sorted(missing_players)}")
        if missing_matches:
            raise ValueError(f"Match CSV is missing columns: {sorted(missing_matches)}")

    def _prepare(self) -> None:
        """Normalize names, dates, rounds and numeric columns once at startup."""
        p = self.players
        m = self.matches

        p["player_name"] = p["player_name"].fillna("").astype(str).str.strip()
        p["team"] = p["team"].fillna("").astype(str).str.strip()
        p["opponent"] = p["opponent"].fillna("").astype(str).str.strip()
        p["round"] = p["round"].fillna("").astype(str).str.strip()
        p["match_date"] = pd.to_datetime(p["match_date"], errors="coerce")

        m["home_team"] = m["home_team"].fillna("").astype(str).str.strip()
        m["away_team"] = m["away_team"].fillna("").astype(str).str.strip()
        m["round"] = m["round"].fillna("").astype(str).str.strip()
        m["venue"] = m["venue"].fillna("").astype(str).str.strip()
        m["match_date"] = pd.to_datetime(m["match_date"], errors="coerce")

        # Explicit numeric coercion protects aggregation functions from mixed
        # string/number input while preserving missing values as NaN.
        player_numeric = [
            "year", "kicks", "marks", "handballs", "disposals", "goals",
            "behinds", "tackles", "inside_50s", "clearances",
            "brownlow_votes", "fantasy_points", "player_score",
        ]
        for col in player_numeric:
            p[col] = pd.to_numeric(p[col], errors="coerce")

        match_numeric = ["season", "home_score", "away_score", "margin", "home_win"]
        for col in match_numeric:
            m[col] = pd.to_numeric(m[col], errors="coerce")

    def _build_indexes(self) -> None:
        """Create small lookup structures so common searches avoid full scans."""
        self.player_names = sorted(
            n for n in self.players["player_name"].dropna().unique() if n
        )
        self.team_names = sorted({
            *[x for x in self.players["team"].dropna().unique() if x],
            *[x for x in self.matches["home_team"].dropna().unique() if x],
            *[x for x in self.matches["away_team"].dropna().unique() if x],
        })

        # Exact normalized name -> canonical name mappings make repeated lookups
        # O(1) for the overwhelmingly common case.
        self.player_name_map = {
            self._norm(n): n for n in self.player_names
        }
        self.team_name_map = {
            self._norm(n): n for n in self.team_names
        }

    @staticmethod
    def _norm(value: str) -> str:
        """Normalize a human-entered name for exact matching."""
        return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()

    @staticmethod
    def _contains_all_tokens(candidate: str, query: str) -> bool:
        """Return True when every query token occurs in the candidate."""
        c = AFLDataStore._norm(candidate)
        q = AFLDataStore._norm(query)
        return bool(q) and all(token in c.split() for token in q.split())

    def resolve_player(self, query: str) -> Optional[ResolvedName]:
        """Resolve a player name using exact, token, substring and fuzzy matching."""
        q = self._norm(query)
        if not q:
            return None

        if q in self.player_name_map:
            return ResolvedName(query, self.player_name_map[q])

        token_hits = [
            n for n in self.player_names
            if self._contains_all_tokens(n, query)
        ]
        if len(token_hits) == 1:
            return ResolvedName(query, token_hits[0])

        substring_hits = [n for n in self.player_names if q in self._norm(n)]
        if len(substring_hits) == 1:
            return ResolvedName(query, substring_hits[0])

        fuzzy = get_close_matches(q, list(self.player_name_map), n=2, cutoff=0.82)
        if len(fuzzy) == 1:
            return ResolvedName(query, self.player_name_map[fuzzy[0]])
        return None

    def resolve_team(self, query: str) -> Optional[ResolvedName]:
        """Resolve a team name using exact, token, substring and fuzzy matching."""
        q = self._norm(query)
        if not q:
            return None

        if q in self.team_name_map:
            return ResolvedName(query, self.team_name_map[q])

        token_hits = [
            n for n in self.team_names
            if self._contains_all_tokens(n, query)
        ]
        if len(token_hits) == 1:
            return ResolvedName(query, token_hits[0])

        substring_hits = [n for n in self.team_names if q in self._norm(n)]
        if len(substring_hits) == 1:
            return ResolvedName(query, substring_hits[0])

        fuzzy = get_close_matches(q, list(self.team_name_map), n=2, cutoff=0.78)
        if len(fuzzy) == 1:
            return ResolvedName(query, self.team_name_map[fuzzy[0]])
        return None

    def player_rows(
        self,
        player_name: str,
        year: Optional[int] = None,
        round_num: Optional[str] = None,
        opponent: Optional[str] = None,
    ) -> tuple[Optional[str], pd.DataFrame]:
        """Return player game rows after deterministic entity resolution."""
        resolved = self.resolve_player(player_name)
        if not resolved:
            return None, self.players.iloc[0:0].copy()

        df = self.players[self.players["player_name"] == resolved.canonical]
        if year is not None:
            df = df[df["year"] == int(year)]
        if round_num:
            df = df[df["round"].str.casefold() == str(round_num).strip().casefold()]
        if opponent:
            opp = self.resolve_team(opponent)
            opp_name = opp.canonical if opp else opponent
            df = df[df["opponent"].str.casefold() == opp_name.casefold()]
        return resolved.canonical, df.copy()

    def team_match_rows(
        self,
        team_name: str,
        year: Optional[int] = None,
        round_num: Optional[str] = None,
        opponent: Optional[str] = None,
    ) -> tuple[Optional[str], pd.DataFrame]:
        """Return team-centric match rows from the supplied match feature table."""
        resolved = self.resolve_team(team_name)
        if not resolved:
            return None, self.matches.iloc[0:0].copy()

        team = resolved.canonical
        home = self.matches["home_team"] == team
        away = self.matches["away_team"] == team
        df = self.matches[home | away].copy()

        if year is not None:
            df = df[df["season"] == int(year)]
        if round_num:
            df = df[df["round"].str.casefold() == str(round_num).strip().casefold()]
        if opponent:
            opp = self.resolve_team(opponent)
            opp_name = opp.canonical if opp else opponent
            df = df[
                ((df["home_team"] == team) & (df["away_team"] == opp_name))
                | ((df["away_team"] == team) & (df["home_team"] == opp_name))
            ]
        return team, df.sort_values(["match_date", "round"], na_position="last").copy()

    @staticmethod
    def _safe_int(value) -> int:
        """Convert a numeric cell to int while treating missing values as zero."""
        return 0 if pd.isna(value) else int(round(float(value)))

    @staticmethod
    def _safe_float(value, digits: int = 1) -> float:
        """Convert a numeric cell to a rounded float while preserving missingness."""
        return 0.0 if pd.isna(value) else round(float(value), digits)

    def player_summary(
        self, player_name: str, year: Optional[int] = None,
        round_num: Optional[str] = None, opponent: Optional[str] = None
    ) -> str:
        """Produce a compact, fully data-backed player aggregate."""
        canonical, df = self.player_rows(player_name, year, round_num, opponent)
        if not canonical:
            return f"Player '{player_name}' was not found in the supplied dataset."
        if df.empty:
            return f"No game records found for {canonical} with the requested filters."

        sums = {}
        for col in [
            "kicks", "marks", "handballs", "disposals", "goals", "behinds",
            "tackles", "inside_50s", "clearances", "brownlow_votes",
        ]:
            sums[col] = self._safe_int(df[col].sum())

        games = len(df)
        avg_disposals = round(sums["disposals"] / games, 1) if games else 0.0
        return (
            f"{canonical} | Games: {games} | Disposals: {sums['disposals']} "
            f"(avg {avg_disposals}/game) | Goals: {sums['goals']} | "
            f"Behinds: {sums['behinds']} | Kicks: {sums['kicks']} | "
            f"Handballs: {sums['handballs']} | Marks: {sums['marks']} | "
            f"Tackles: {sums['tackles']} | Inside 50s: {sums['inside_50s']} | "
            f"Clearances: {sums['clearances']} | Brownlow votes: {sums['brownlow_votes']}."
        )

    def player_game_log(
        self, player_name: str, year: Optional[int] = None, limit: int = 10
    ) -> str:
        """Return recent/chronological player game rows for drill-down questions."""
        canonical, df = self.player_rows(player_name, year)
        if not canonical:
            return f"Player '{player_name}' was not found in the supplied dataset."
        if df.empty:
            return f"No game records found for {canonical}."

        df = df.sort_values("match_date").tail(max(1, min(int(limit), 50)))
        lines = [f"{canonical} game log ({len(df)} rows shown):"]
        for _, r in df.iterrows():
            date = r["match_date"].strftime("%Y-%m-%d") if pd.notna(r["match_date"]) else "unknown date"
            lines.append(
                f"- {date}, Round {r['round']}, vs {r['opponent']}, result {r['result']}, "
                f"disposals {self._safe_int(r['disposals'])}, goals {self._safe_int(r['goals'])}, "
                f"tackles {self._safe_int(r['tackles'])}, fantasy {self._safe_int(r['fantasy_points'])}."
            )
        return "\n".join(lines)


    def player_leaders(
        self,
        stat: str,
        year: Optional[int] = None,
        top_n: int = 10,
    ) -> str:
        """
        Aggregate a supported player statistic and return a
        deterministic ranked leaderboard.

        Important data-quality behavior:

        - filters by season when requested;
        - validates the requested statistic;
        - removes records without a real player identity;
        - groups using player_id and player_name;
        - sorts highest to lowest;
        - limits the result to 25 players.

        This prevents unidentified aggregate rows from being
        reported as real AFL players.
        """

        # --------------------------------------------------------
        # Supported statistic aliases.
        # --------------------------------------------------------

        aliases = {
            "disposal": "disposals",
            "disposals": "disposals",

            "goal": "goals",
            "goals": "goals",

            "behind": "behinds",
            "behinds": "behinds",

            "kick": "kicks",
            "kicks": "kicks",

            "mark": "marks",
            "marks": "marks",

            "tackle": "tackles",
            "tackles": "tackles",

            "handball": "handballs",
            "handballs": "handballs",

            "fantasy": "fantasy_points",
            "fantasy points": "fantasy_points",

            "clearance": "clearances",
            "clearances": "clearances",
        }

        key = aliases.get(
            str(stat).strip().lower()
        )

        if not key:
            return (
                "Unsupported statistic. Use disposals, goals, "
                "behinds, kicks, marks, tackles, handballs, "
                "fantasy points, or clearances."
            )

        # --------------------------------------------------------
        # Validate leaderboard size.
        # --------------------------------------------------------

        try:
            requested_top_n = int(top_n)
        except (TypeError, ValueError):
            requested_top_n = 10

        requested_top_n = max(
            1,
            min(requested_top_n, 25),
        )

        # --------------------------------------------------------
        # Work on a copy.
        # --------------------------------------------------------

        df = self.players.copy()

        # --------------------------------------------------------
        # Filter season.
        # --------------------------------------------------------

        if year is not None:
            df = df[
                df["year"] == int(year)
            ]

        if df.empty:
            return (
                f"No player records found for "
                f"{year or 'the requested period'}."
            )

        # --------------------------------------------------------
        # Verify required columns.
        # --------------------------------------------------------

        required_columns = {
            "player_id",
            "player_name",
            key,
        }

        missing = (
            required_columns
            - set(df.columns)
        )

        if missing:
            return (
                "Unable to build leaderboard because the dataset "
                "is missing required columns: "
                + ", ".join(sorted(missing))
                + "."
            )

        # --------------------------------------------------------
        # Clean player names.
        # --------------------------------------------------------

        df["player_name"] = (
            df["player_name"]
            .astype("string")
            .str.strip()
        )

        # --------------------------------------------------------
        # Clean player IDs.
        # --------------------------------------------------------

        player_id_text = (
            df["player_id"]
            .astype("string")
            .str.strip()
            .str.lower()
        )

        # --------------------------------------------------------
        # Remove invalid player identities.
        # --------------------------------------------------------

        valid_id = (
            df["player_id"].notna()
            & ~player_id_text.isin(
                [
                    "",
                    "nan",
                    "none",
                    "null",
                ]
            )
        )

        valid_name = (
            df["player_name"].notna()
            & ~df["player_name"].str.lower().isin(
                [
                    "",
                    "nan",
                    "none",
                    "null",
                    "unknown",
                ]
            )
        )

        df = df[
            valid_id
            & valid_name
        ].copy()

        if df.empty:
            return (
                f"No valid player records found for "
                f"{year or 'the requested period'}."
            )

        # --------------------------------------------------------
        # Make statistic numeric.
        # --------------------------------------------------------

        df[key] = pd.to_numeric(
            df[key],
            errors="coerce",
        )

        df = df[
            df[key].notna()
        ].copy()

        if df.empty:
            return (
                f"No valid {key.replace('_', ' ')} data found "
                f"for {year or 'the requested period'}."
            )

        # --------------------------------------------------------
        # Aggregate by player identity.
        # --------------------------------------------------------

        grouped = (
            df.groupby(
                [
                    "player_id",
                    "player_name",
                ],
                as_index=False,
                dropna=False,
            )[key]
            .sum()
        )

        # --------------------------------------------------------
        # Highest statistic first.
        # --------------------------------------------------------

        grouped = grouped.sort_values(
            by=key,
            ascending=False,
            kind="stable",
        )

        grouped = grouped.head(
            requested_top_n
        )

        # --------------------------------------------------------
        # Build human-readable response.
        # --------------------------------------------------------

        stat_label = key.replace(
            "_",
            " ",
        )

        if year is not None:
            scope = f"in {year}"
        else:
            scope = "across all supplied seasons"

        lines = [
            f"Top {len(grouped)} "
            f"{stat_label} leaders {scope}:"
        ]

        for rank, (_, row) in enumerate(
            grouped.iterrows(),
            start=1,
        ):

            player_name = str(
                row["player_name"]
            ).strip()

            value = self._safe_int(
                row[key]
            )

            lines.append(
                f"{rank}. {player_name} — "
                f"{value} {stat_label}"
            )

        return "\n".join(lines)

    def team_summary(self, team_name: str, year: Optional[int] = None) -> str:
        """Calculate team record, scoring and margin from match-level features."""
        canonical, df = self.team_match_rows(team_name, year)
        if not canonical:
            return f"Team '{team_name}' was not found in the supplied dataset."
        if df.empty:
            return f"No match records found for {canonical} with the requested filters."

        rows = []
        for _, r in df.iterrows():
            is_home = r["home_team"] == canonical
            team_score = r["home_score"] if is_home else r["away_score"]
            opp_score = r["away_score"] if is_home else r["home_score"]
            result = "W" if team_score > opp_score else ("L" if team_score < opp_score else "D")
            rows.append((result, float(team_score), float(opp_score)))

        wins = sum(x[0] == "W" for x in rows)
        losses = sum(x[0] == "L" for x in rows)
        draws = sum(x[0] == "D" for x in rows)
        avg_for = sum(x[1] for x in rows) / len(rows)
        avg_against = sum(x[2] for x in rows) / len(rows)
        avg_margin = avg_for - avg_against
        pct = 100 * wins / len(rows)

        scope = f"in {year}" if year else "across all supplied seasons"
        return (
            f"{canonical} {scope} | Games: {len(rows)} | Wins: {wins} | "
            f"Losses: {losses} | Draws: {draws} | Win rate: {pct:.1f}% | "
            f"Avg score for: {avg_for:.1f} | Avg score against: {avg_against:.1f} | "
            f"Avg margin: {avg_margin:.1f}."
        )

    def team_form(self, team_name: str, year: int, last_n: int = 5) -> str:
        """Show the latest N matches for a team in a season."""
        canonical, df = self.team_match_rows(team_name, year)
        if not canonical:
            return f"Team '{team_name}' was not found in the supplied dataset."
        if df.empty:
            return f"No match records found for {canonical} in {year}."

        df = df.tail(max(1, min(int(last_n), 10)))
        lines = [f"Latest {len(df)} {year} matches for {canonical}:"]
        for _, r in df.iterrows():
            is_home = r["home_team"] == canonical
            opp = r["away_team"] if is_home else r["home_team"]
            team_score = self._safe_int(r["home_score"] if is_home else r["away_score"])
            opp_score = self._safe_int(r["away_score"] if is_home else r["home_score"])
            outcome = "W" if team_score > opp_score else ("L" if team_score < opp_score else "D")
            lines.append(
                f"- Round {r['round']} vs {opp}: {outcome}, {team_score}-{opp_score}, "
                f"margin {abs(team_score - opp_score)}."
            )
        return "\n".join(lines)

    def match_lookup(
        self, team_a: str, year: Optional[int] = None,
        round_num: Optional[str] = None, team_b: Optional[str] = None
    ) -> str:
        """Find exact team matchups, optionally filtered by season and round."""
        a = self.resolve_team(team_a)
        if not a:
            return f"Team '{team_a}' was not found in the supplied dataset."

        df = self.matches[
            (self.matches["home_team"] == a.canonical)
            | (self.matches["away_team"] == a.canonical)
        ].copy()

        if team_b:
            b = self.resolve_team(team_b)
            b_name = b.canonical if b else team_b
            df = df[
                ((df["home_team"] == a.canonical) & (df["away_team"] == b_name))
                | ((df["away_team"] == a.canonical) & (df["home_team"] == b_name))
            ]
        if year is not None:
            df = df[df["season"] == int(year)]
        if round_num:
            df = df[df["round"].str.casefold() == str(round_num).casefold()]

        if df.empty:
            return "No matching AFL match was found in the supplied dataset."

        lines = [f"Match results ({min(len(df), 20)} shown):"]
        for _, r in df.sort_values("match_date").head(20).iterrows():
            outcome = "Draw"
            if r["home_score"] > r["away_score"]:
                outcome = f"{r['home_team']} won"
            elif r["home_score"] < r["away_score"]:
                outcome = f"{r['away_team']} won"
            lines.append(
                f"- {r['match_date'].strftime('%Y-%m-%d') if pd.notna(r['match_date']) else 'unknown date'} "
                f"Round {r['round']}: {r['home_team']} {self._safe_int(r['home_score'])} - "
                f"{self._safe_int(r['away_score'])} {r['away_team']} at {r['venue']} ({outcome})."
            )
        return "\n".join(lines)

    def head_to_head(self, team_a: str, team_b: str, start_year: Optional[int] = None, end_year: Optional[int] = None) -> str:
        """Summarize wins and scoring for two teams across their meetings."""
        a = self.resolve_team(team_a)
        b = self.resolve_team(team_b)
        if not a or not b:
            return "One or both teams were not found in the supplied dataset."

        df = self.matches[
            ((self.matches["home_team"] == a.canonical) & (self.matches["away_team"] == b.canonical))
            | ((self.matches["home_team"] == b.canonical) & (self.matches["away_team"] == a.canonical))
        ].copy()
        if start_year is not None:
            df = df[df["season"] >= int(start_year)]
        if end_year is not None:
            df = df[df["season"] <= int(end_year)]

        if df.empty:
            return f"No head-to-head matches found for {a.canonical} and {b.canonical}."

        a_wins = b_wins = draws = 0
        for _, r in df.iterrows():
            if r["home_score"] == r["away_score"]:
                draws += 1
            elif (
                (r["home_team"] == a.canonical and r["home_score"] > r["away_score"])
                or (r["away_team"] == a.canonical and r["away_score"] > r["home_score"])
            ):
                a_wins += 1
            else:
                b_wins += 1

        scope = ""
        if start_year is not None or end_year is not None:
            scope = f" ({start_year or 'earliest'}-{end_year or 'latest'})"
        return (
            f"Head-to-head{scope}: {a.canonical} wins {a_wins}, "
            f"{b.canonical} wins {b_wins}, draws {draws}, meetings {len(df)}."
        )


# A single store instance keeps the large player table in memory only once.
STORE = AFLDataStore()
