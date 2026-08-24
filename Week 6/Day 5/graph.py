"""
graph.py
========
Sections 12-18: retrieval/prediction/direct-AFL nodes, validation node,
clarification/fallback node, response formatting node, and the compiled
LangGraph itself with conditional edges and a checkpointer for multi-turn
memory.

============================================================
ASCII GRAPH
============================================================

                         START
                           |
                           v
                    LOAD_CONTEXT
                           |
                           v
                       ROUTER  (intent + entity extraction, merges prior turn context)
                           |
        -----------------------------------------------------------
        |            |              |            |         |      |
        v            v              v            v         v      v
   RETRIEVAL   PRED_MATCH    PRED_PLAYER    DIRECT_AFL  OFF_TOPIC AMBIGUOUS
        |            |              |            |         |      |
        v            v              v            v         |      |
   (calls a       (calls        (calls       (LLM/canned    |      |
    retrieval_     predict_      predict_      explanation,  |      |
    tools fn        match_        top_          routed to    |      |
    based on        winner)       player)       retrieval    |      |
    entities)                                    instead if   |      |
        |            |              |            numeric)     |      |
        -----------------------------------------              |      |
                     |                                          |      |
                     v                                          v      v
                VALIDATION                                REFUSAL  CLARIFICATION
                     |                                       NODE      NODE
           ------------------                                  |         |
           |                |                                  v         v
           v                v                                 END       END
        SUCCESS      FAILURE/AMBIGUOUS
           |                |
           v                v
   RESPONSE_FORMATTER  CLARIFICATION_NODE
           |                |
           v                v
          END              END

Every retrieval/prediction/direct_afl path funnels through ONE shared
validation node before the response is ever formatted -- validation is
never bypassed by a specific node, and clarification/refusal are terminal
(no retry loops -- see MAX_RETRIES).

============================================================
CAPSTONE ADDITIONS (v2) -- see inline comments for detail
============================================================
1. BUG FIX -- stale entity carryover (router_node): entities extracted
   FROM THIS TURN's own text now always take priority over whatever was
   carried from a previous turn, and prior entities of a kind (player,
   team/opponent) are explicitly CLEARED -- not just left unmentioned --
   whenever this turn stands on its own without a continuation cue
   (a pronoun like "his"/"he", or a phrase like "that match"/"what
   about"). This is what previously let a stale `player` entity leak into
   an unrelated team-vs-team question, and made "who won the 1950 Grand
   Final?" silently try (and fail) to look up a stale player instead of
   asking for a team.
2. Two-player comparison, multi-season aggregation, and single-game-high
   routing added to retrieval_node, backed by the new retrieval_tools
   functions.
3. Hardening (Task 1): every tool call goes through _safe_call(), which
   applies a hard timeout and converts ANY exception into the tool's own
   {ok: False, error: ...} shape instead of ever crashing the graph. A
   single consistent prediction disclaimer is now applied centrally in
   response_formatter_node instead of being duplicated (and risking
   drifting out of sync) across three separate formatter functions.
4. Observability (Task 3): router_node records which classifier actually
   answered (`router_source`: "gemini" or "rule_based") and load_context/
   response_formatter record wall-clock latency, both surfaced in the
   returned state for the FastAPI layer to log.
"""
from __future__ import annotations
import os
import re
import time
import concurrent.futures
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from state import AFLState
from router import route_query, is_prompt_injection_attempt
from resolvers import resolve_team, resolve_player
from entity_extraction import find_team_candidates, find_player_candidates, find_years
from router import _extract_year, _extract_before_year, _extract_round, _extract_stat, _extract_venue_scope, _extract_top_n
import retrieval_tools as rt
import prediction_tools as pt
from grounding import check_grounding

MAX_RETRIES = 1  # no infinite clarification loops
TOOL_TIMEOUT_SECONDS = float(os.environ.get("AFL_TOOL_TIMEOUT_SECONDS", "8"))
_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="afl-tool")

_PREDICTION_DISCLAIMER = "This is a model-predicted estimate, not a certainty."


def _safe_call(fn, *args, timeout: float = TOOL_TIMEOUT_SECONDS, **kwargs) -> dict:
    """Run a retrieval/prediction tool call with a hard timeout and never
    let an unexpected exception crash the graph -- always returns the
    tool's own {ok: False, error/clarification: ...} shape instead of
    raising. This is the single choke point Task-1 hardening flows
    through: every rt.* / pt.* call in this file goes through here."""
    future = _EXECUTOR.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {"ok": False, "error": f"The '{fn.__name__}' lookup took too long and was stopped. Please try again."}
    except Exception as e:  # noqa: BLE001 -- intentionally broad: this is the last line of defense
        return {"ok": False, "error": f"Something went wrong running '{fn.__name__}': {e}"}


# ---------------------------------------------------------------------------
# LOAD_CONTEXT
# ---------------------------------------------------------------------------
def load_context_node(state: AFLState) -> dict:
    trace = [f"LOAD_CONTEXT: query='{state['user_query']}'"]
    entities = dict(state.get("extracted_entities") or {})
    return {"trace": trace, "extracted_entities": entities, "retry_count": state.get("retry_count", 0)}


# ---------------------------------------------------------------------------
# ROUTER
# ---------------------------------------------------------------------------
_CONTINUATION_MARKERS = [
    " he ", " his ", " him ", "he's", " she ", " her ",
    "in that match", "in that game", "that match", "that game",
    "same match", "same game", "what about", "and what", "and how",
    "and did", "also", " it ", "did he", "was he", "does he", "did she",
    "round before", "previous round", "prior round", "round after",
    "next round", "following round", "round prior",
]
_RELATIVE_ROUND_RE = re.compile(
    r"\b(round before|previous round|prior round|round after|next round|following round|round prior)\b"
)


def _has_continuation_marker(ql: str) -> bool:
    padded = f" {ql} "
    return any(m in padded for m in _CONTINUATION_MARKERS)


def router_node(state: AFLState) -> dict:
    """
    Classifies intent AND decides which entities from the PREVIOUS turn are
    still relevant. See module docstring point (1) -- this is the fix for
    the stale entity carryover bug: this turn's own extracted entities
    always win, and unrelated stale entities are explicitly cleared (not
    just left un-overwritten) whenever the turn stands on its own.
    """
    query = state["user_query"]
    history = state.get("messages", [])
    prior_entities = state.get("extracted_entities") or {}
    ql = query.lower()

    result, router_source = route_query(query, history, prior_entities)

    current_team_cands = find_team_candidates(query)
    current_player_cands = find_player_candidates(query, max_candidates=2)
    current_years = find_years(query)
    has_matchup_word = any(w in ql for w in [" vs ", " v ", "beat", " against ", "versus", "compare"])
    continuation = _has_continuation_marker(ql)

    # Entities THIS turn's own text actually supports (router output takes
    # priority over conservative candidate extraction, since the router
    # already validated it via resolve_team/resolve_player where relevant).
    #
    # IMPORTANT: rule_based_route() itself falls back to
    # prior_entities.get("year"/"round"/"stat") internally (documented,
    # intentional carry-forward machinery) and returns that baked-in value
    # in RouterOutput regardless of whether THIS turn's text actually
    # mentioned it. Trusting result.year/round/stat directly here would
    # silently re-authorize a stale value as if this turn had just
    # provided it, defeating the clearing logic below entirely (this is
    # exactly what caused a stale 'stat' to leak into an unrelated
    # team-vs-team query). So for the rule-based path we recompute
    # "own_*" directly from this turn's own text and use THAT instead --
    # except for round, where a resolved relative reference ("the round
    # before that") is a genuine, deliberate one-turn-old-info decrement,
    # not a stale leak, so it's still honored.
    if router_source == "gemini":
        # Gemini receives conversation history and may return an entity it
        # believes is implied. On a fresh message, only text-supported values
        # are allowed; implied carry-forward is valid only for continuations.
        current_year = result.year if (_extract_year(ql) is not None or continuation) else None
        current_round = result.round if (_extract_round(ql) is not None or continuation) else None
        current_stat = result.stat if (_extract_stat(ql) is not None or continuation) else None
    else:
        own_year, own_round, own_stat = _extract_year(ql), _extract_round(ql), _extract_stat(ql)
        current_year = own_year
        current_stat = own_stat
        if own_round is not None:
            current_round = own_round
        elif _RELATIVE_ROUND_RE.search(ql):
            current_round = result.round  # legitimate relative-round resolution
        else:
            current_round = None

    current = {}
    if current_year is not None:
        current["year"] = current_year
    if result.before_year is not None and (_extract_before_year(ql) is not None or continuation):
        current["before_year"] = result.before_year
    if current_round is not None:
        current["round"] = current_round
    if current_stat is not None:
        current["stat"] = current_stat
    if result.venue_scope is not None and (_extract_venue_scope(ql) is not None or continuation):
        current["venue_scope"] = result.venue_scope
    if result.top_n is not None and (_extract_top_n(ql) is not None or continuation):
        current["top_n"] = result.top_n
    if result.prediction_type is not None:
        current["prediction_type"] = result.prediction_type

    if result.team and (current_team_cands or continuation):
        current["team"] = result.team
    elif current_team_cands:
        current["team"] = current_team_cands[0]

    if result.opponent and (len(current_team_cands) >= 2 or continuation):
        current["opponent"] = result.opponent
    elif len(current_team_cands) >= 2:
        current["opponent"] = current_team_cands[1]

    if result.player and (current_player_cands or continuation):
        current["player"] = result.player
    elif current_player_cands:
        current["player"] = current_player_cands[0]

    if len(current_player_cands) >= 2:
        current["player2"] = current_player_cands[1]

    if len(current_years) >= 2:
        current["years"] = current_years

    team_ambiguous = None
    if not current.get("team") and len(current_team_cands) >= 2 and not has_matchup_word:
        team_ambiguous = current_team_cands

    merged = dict(prior_entities)
    merged.pop("team_ambiguous_candidates", None)
    merged.pop("player_ambiguous_candidates", None)

    turn_has_own_team_ref = bool(current.get("team")) or bool(current.get("opponent"))
    turn_has_own_player_ref = bool(current.get("player"))

    if not continuation:
        # This turn stands on its own -- don't let unrelated context from
        # an earlier, different question leak into the answer.
        if turn_has_own_team_ref and not turn_has_own_player_ref:
            # e.g. "Carlton's win rate against Collingwood" arriving right
            # after a player question -- a fresh team-oriented question
            # must not keep answering about the old player.
            merged.pop("player", None)
            merged.pop("player2", None)
        if turn_has_own_player_ref and not turn_has_own_team_ref:
            # e.g. an explicitly-named new player question shouldn't keep
            # an unrelated old team/opponent pairing either.
            merged.pop("team", None)
            merged.pop("opponent", None)
        if not turn_has_own_team_ref and not turn_has_own_player_ref and result.intent not in (
                "direct_afl", "off_topic", "ambiguous"):
            # No entity of its own at all (e.g. "Who won the 1950 Grand
            # Final?") -- clear ALL stale team/player context so the
            # system asks for what's actually missing (a team) instead of
            # silently reusing whoever was last discussed.
            merged.pop("player", None)
            merged.pop("team", None)
            merged.pop("opponent", None)
            merged.pop("player2", None)
        # A stale second-player/multi-season list from an OLDER comparison
        # should not silently survive into an unrelated fresh question.
        if "player2" not in current:
            merged.pop("player2", None)
        if "years" not in current:
            merged.pop("years", None)
        # Same principle for `stat` and `round`: a fresh (non-continuation)
        # turn that doesn't mention its own stat/round shouldn't silently
        # keep answering using whatever stat/round an earlier, unrelated
        # question happened to set. This is what let a stale 'disposals'
        # stat hijack a later team-match lookup into a top-player lookup.
        if "stat" not in current:
            merged.pop("stat", None)
        if "round" not in current:
            merged.pop("round", None)
        if "year" not in current:
            merged.pop("year", None)
        if "before_year" not in current:
            merged.pop("before_year", None)
        if "venue_scope" not in current:
            merged.pop("venue_scope", None)
        if len(current_player_cands) < 2 and "player2" not in current:
            merged.pop("player2", None)

    # This turn's own values always win over whatever survived clearing.
    for field, val in current.items():
        merged[field] = val

    if team_ambiguous:
        merged["team_ambiguous_candidates"] = team_ambiguous
        merged.pop("team", None)

    trace = [f"ROUTER: intent={result.intent} source={router_source} "
             f"entities={ {k: v for k, v in merged.items() if not k.endswith('candidates')} } "
             f"reasoning={result.reasoning}"]
    return {"intent": result.intent, "extracted_entities": merged, "trace": trace,
            "router_source": router_source}


def route_decision(state: AFLState) -> str:
    return state["intent"]


# ---------------------------------------------------------------------------
# RETRIEVAL NODE
# ---------------------------------------------------------------------------
_H2H_MARKERS = ["head", "record", "history", "win rate", "win percentage", "record against",
                "h2h", "beaten", "how have they done against"]
_SINGLE_GAME_HIGH_MARKERS = ["highest", "best game", "career high", "career-high",
                             "biggest game", "most ever"]
_COMPARE_MARKERS = [" vs ", " v ", "versus", "compare", "who has more", "who had more"]
_MULTI_SEASON_MARKERS = ["combined", "across", "over the last", "over the past"]


def retrieval_node(state: AFLState) -> dict:
    q = state["user_query"].lower()
    e = state.get("extracted_entities") or {}
    trace = []

    if e.get("team_ambiguous_candidates") and not e.get("team"):
        cands = e["team_ambiguous_candidates"]
        result = {"ok": False, "clarification":
                  f"That could refer to multiple teams: {', '.join(cands)}. Which one did you mean?"}
        trace.append("RETRIEVAL_NODE: ambiguous team reference")
        return {"tool_result": result, "tool_called": None, "trace": trace}

    team, opponent = e.get("team"), e.get("opponent")
    player, player2 = e.get("player"), e.get("player2")
    year, years, round_, stat = e.get("year"), e.get("years"), e.get("round"), e.get("stat")
    venue_scope = e.get("venue_scope")

    is_h2h = any(m in q for m in _H2H_MARKERS)
    is_single_high = any(m in q for m in _SINGLE_GAME_HIGH_MARKERS)
    is_compare = (player2 is not None) or any(m in q for m in _COMPARE_MARKERS)
    is_multi_season = any(m in q for m in _MULTI_SEASON_MARKERS) or bool(years and len(years) >= 2)

    tool_name, result = None, None
    query_team_candidates = find_team_candidates(state["user_query"])

    if (state.get("intent") == "retrieval" and year and len(query_team_candidates) >= 3
            and "more often than" in q):
        tool_name = "compare_team_head_to_heads"
        result = _safe_call(rt.compare_team_head_to_heads, query_team_candidates[0],
                            query_team_candidates[1], query_team_candidates[2], year)

    if result is not None:
        pass
    elif state.get("intent") == "top_goal_scorers":
        tool_name = "get_top_goal_scorers"
        result = _safe_call(rt.get_top_goal_scorers, year)
    elif state.get("intent") == "multi_part":
        parts = []
        if team and opponent and any(marker in q for marker in ["win rate", "head to head", "record"]):
            parts.append(("get_team_head_to_head", _safe_call(
                rt.get_team_head_to_head, team, opponent, venue_scope=venue_scope)))
        if team and opponent and any(marker in q for marker in ["predict", "who wins", "will win", "beat"]):
            parts.append(("predict_match_winner", _safe_call(
                pt.predict_match_winner, team, opponent, fixture_confirmed=False)))
        elif team and any(marker in q for marker in ["predict", "prediction"]):
            if any(marker in q for marker in ["goal", "goalscorer", "goal scorer"]):
                parts.append(("predict_top_goal_scorers", _safe_call(
                    pt.predict_top_player, team, prediction_type="expected_goals", limit=e.get("top_n", 5))))
            else:
                parts.append(("get_team_overview", _safe_call(rt.get_team_overview, team, year=year)))
        if year and any(marker in q for marker in ["top goal", "top scorer", "goal scorer", "goalscorer"]):
            parts.append(("get_top_goal_scorers", _safe_call(rt.get_top_goal_scorers, year)))
        if player and year and stat and any(marker in q for marker in ["predict", "expected", "will"]):
            prediction_type = pt.stat_to_prediction_type(stat)
            if prediction_type:
                parts.append(("get_player_season_stats", _safe_call(rt.get_player_season_stats, player, year)))
                parts.append(("predict_player_stat_value", _safe_call(
                    pt.predict_player_stat_value, player, prediction_type=prediction_type)))
        if not parts:
            result = {"ok": False, "clarification": "Please provide the teams or players and details for each AFL question."}
        else:
            tool_name = "multi_part_tools"
            result = {"ok": True, "data": {"results": parts}}

            if len(parts) < 2:
                result["data"]["note"] = ("I can provide the team statistics, but I need the opponent "
                                             "for the match prediction.")

    elif state.get("intent") == "upcoming_fixtures":
        tool_name = "upcoming_fixtures"
        result = {"ok": False, "error": "The available AFL dataset contains historical matches only; upcoming fixture information is unavailable."}

    elif state.get("intent") == "retrieval_overview":
        if not team:
            result = {"ok": False, "clarification": "Which team would you like an overview of?"}
        else:
            tool_name = "get_team_overview"
            result = _safe_call(rt.get_team_overview, team, year=year)

    elif state.get("intent") == "recent_team_stats":
        if not team:
            result = {"ok": False, "clarification": "Which team's recent games should I look up?"}
        else:
            match = re.search(r"\b(?:last|recent)\s+(\d+)\s+(?:games?|matches?)\b", q)
            game_count = int(match.group(1)) if match else 10
            tool_name = "get_team_recent_stats"
            result = _safe_call(rt.get_team_recent_stats, team, games=game_count,
                                before_year=e.get("before_year"))

    elif not player and years and len(years) >= 2 and ("win rate" in q or "win percentage" in q):
        tool_name = "get_best_team_win_rate"
        result = _safe_call(rt.get_best_team_win_rate, years)

    # 1. Two-player comparison -- checked first so a comparison question is
    #    never silently collapsed into a single-player answer.
    elif player and player2 and is_compare:
        tool_name = "compare_players"
        result = _safe_call(rt.compare_players, player, player2, year=year, stat=stat)

    # 2. Single-game career/season high for a named player -- never
    #    silently substitutes a season average for this.
    elif player and stat and is_single_high:
        tool_name = "get_player_single_game_high"
        result = _safe_call(rt.get_player_single_game_high, player, stat, year=year)

    # 3. Multi-season combined stats for a named player.
    elif player and is_multi_season and years and len(years) >= 2:
        tool_name = "get_player_multi_season_stats"
        result = _safe_call(rt.get_player_multi_season_stats, player, years, stat=stat)
    elif player and is_multi_season and not (years and len(years) >= 2):
        tool_name, result = None, {
            "ok": False,
            "clarification": (f"Which seasons should I combine for {player}? Please name at least "
                               f"two years (e.g. '2022 and 2023').")
        }

    elif player and year and ("season average" in q or "season total" in q or "season stats" in q
                                or ("season" in q and "average" in q)):
        tool_name, result = "get_player_season_stats", _safe_call(rt.get_player_season_stats, player, year)
    elif player and not stat and ("season" in q or "average" in q or "total" in q) and year and "round" not in q and not round_:
        tool_name, result = "get_player_season_stats", _safe_call(rt.get_player_season_stats, player, year)

    # 4. Explicit single match between two named teams in a specific round.
    elif team and opponent and round_ and year and not player and not is_h2h:
        tool_name, result = "get_team_match_in_round", _safe_call(rt.get_team_match_in_round, team, year, round_)

    # 5. Team vs team head-to-head (broadened trigger set; also the default
    #    when two teams are named but no specific round/year narrows it to
    #    a single match).
    elif team and opponent and not player and (is_h2h or not (year and round_)):
        tool_name, result = "get_team_head_to_head", _safe_call(
            rt.get_team_head_to_head, team, opponent, venue_scope=venue_scope)

    elif player and year and round_:
        tool_name, result = "get_player_match_stats", _safe_call(rt.get_player_match_stats, player, year, round_)
    elif stat and team and year and round_:
        tool_name, result = "get_top_player_in_match", _safe_call(rt.get_top_player_in_match, team, year, round_, stat)
    elif ("most disposals" in q or "most goals" in q or "top player" in q) and team and year and round_:
        stat2 = stat or ("goals" if "goals" in q else "disposals")
        tool_name, result = "get_top_player_in_match", _safe_call(rt.get_top_player_in_match, team, year, round_, stat2)
    elif team and year and round_:
        tool_name, result = "get_team_match_in_round", _safe_call(rt.get_team_match_in_round, team, year, round_)
    elif player and year:
        tool_name, result = "get_player_season_stats", _safe_call(rt.get_player_season_stats, player, year)
    else:
        missing = []
        if "grand final" in q and year and not team and not player:
            result = {"ok": False, "error": (
                f"The available dataset cannot verify the {year} AFL Grand Final without a supported "
                "historical event lookup. No team is required, but that event is outside the retrievable data path.")}
            trace.append("RETRIEVAL_NODE: historical event unavailable without team")
            return {"tool_result": result, "tool_called": None, "trace": trace}
        if not team and not player:
            missing.append("a team or player")
        if not year and not (team and opponent):
            missing.append("a year/season")
        if not round_ and team and not opponent and not ("season" in q or "average" in q):
            missing.append("a round")
        tool_name, result = None, {
            "ok": False,
            "clarification": (
                f"I need {', and '.join(missing) if missing else 'more detail'} to look that up "
                f"exactly. Could you provide it? (If you're asking about a result like a Grand "
                f"Final, please also name a team.)"
            )
        }

    trace.append(f"RETRIEVAL_NODE: tool={tool_name} result_ok={result.get('ok')}")

    # Carry the resolved player forward into entities so a later pronoun
    # ("his disposals", "compare with his season average") can be resolved
    # without the user re-stating the name.
    updated_entities = dict(e)
    if result.get("ok") and isinstance(result.get("data"), dict) and result["data"].get("player"):
        updated_entities["player"] = result["data"]["player"]
    if result.get("ok") and isinstance(result.get("data"), dict) and result["data"].get("round"):
        updated_entities["round"] = str(result["data"]["round"])

    return {"tool_result": result, "tool_called": tool_name, "trace": trace,
            "extracted_entities": updated_entities}


# ---------------------------------------------------------------------------
# PREDICTION NODES
# ---------------------------------------------------------------------------
def prediction_match_node(state: AFLState) -> dict:
    e = state.get("extracted_entities") or {}
    home = e.get("team") or e.get("home_team")
    away = e.get("opponent") or e.get("away_team")
    trace = []
    if not home or not away:
        result = {"ok": False, "clarification": "I need both teams in the matchup to make a prediction. Which two teams?"}
        trace.append("PREDICTION_MATCH_NODE: missing team(s)")
        return {"tool_result": result, "tool_called": "predict_match_winner", "trace": trace}

    result = _safe_call(pt.predict_match_winner, home, away, fixture_confirmed=False)
    trace.append(f"PREDICTION_MATCH_NODE: home={home} away={away} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_match_winner", "trace": trace}


def prediction_premiership_node(state: AFLState) -> dict:
    """Season/league-wide 'who will win the AFL/premiership/flag [in <year>]'
    queries -- routed separately from prediction_match_node since there is
    no single named matchup, only (optionally) a target season."""
    e = state.get("extracted_entities") or {}
    trace = []
    result = _safe_call(pt.predict_premiership_favourite, season=e.get("year"), timeout=TOOL_TIMEOUT_SECONDS * 3)
    trace.append(f"PREDICTION_PREMIERSHIP_NODE: season={e.get('year')} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_premiership_favourite", "trace": trace}


def prediction_player_node(state: AFLState) -> dict:
    e = state.get("extracted_entities") or {}
    team = e.get("team")
    opponent = e.get("opponent")
    player = e.get("player")
    stat = e.get("stat")
    pred_type = e.get("prediction_type")
    q = (state["user_query"] or "").lower()
    trace = []

    # Map a recognized stat word ("goals", "kicks", ...) onto its actual
    # supported prediction_type instead of always defaulting to disposals
    # -- this is what lets "predict Geelong's top goalkicker" reach the
    # goals regressor instead of silently answering about disposals.
    if not pred_type or pred_type in ("top_disposals", None):
        mapped = pt.stat_to_prediction_type(stat) if stat else None
        if mapped:
            pred_type = mapped
        elif "goal" in q:
            pred_type = "expected_goals" if "expected" in q or "predicted" in q else "top_disposals"
            if "goal" in q and "top_disposals" == pred_type:
                # "who will top-score" style phrasing about goals specifically
                pred_type = "expected_goals"
        elif "kick" in q:
            pred_type = "expected_kicks"
        elif "mark" in q and "bookmark" not in q:
            pred_type = "expected_marks"
        elif "handball" in q:
            pred_type = "expected_handballs"
        elif "tackle" in q:
            pred_type = "expected_tackles"
        elif "disposal" in q and ("expected" in q or "predicted disposals" in q):
            pred_type = "expected_disposals"
        else:
            pred_type = pred_type or "top_disposals"

    # A single named player + a supported regression stat -> point
    # prediction for that one player, rather than a whole-team ranking.
    if player and pred_type in pt.PLAYER_PREDICTION_SPECS and pt.PLAYER_PREDICTION_SPECS[pred_type]["kind"] == "regression":
        result = _safe_call(pt.predict_player_stat_value, player, prediction_type=pred_type)
        trace.append(f"PREDICTION_PLAYER_NODE: single-player player={player} type={pred_type} ok={result.get('ok')}")
        return {"tool_result": result, "tool_called": "predict_player_stat_value", "trace": trace}

    if not team:
        result = {"ok": False, "clarification": "Which team's players should I predict for?"}
        trace.append("PREDICTION_PLAYER_NODE: missing team")
        return {"tool_result": result, "tool_called": "predict_top_player", "trace": trace}

    result = _safe_call(pt.predict_top_player, team, opponent=opponent,
                        prediction_type=pred_type, limit=e.get("top_n", 5))
    trace.append(f"PREDICTION_PLAYER_NODE: team={team} type={pred_type} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_top_player", "trace": trace}


# ---------------------------------------------------------------------------
# DIRECT AFL NODE
_AFL_GLOSSARY = {
    "holding the ball": "A free kick paid against a tackled player who had prior opportunity to dispose "
                         "of the ball correctly (by hand or foot) but failed to do so before or during the tackle.",
    "holding the man": "A free kick paid against a player who tackles or holds an opponent who no longer has "
                        "the ball.",
    "mark": "A mark is awarded when a player cleanly catches (or takes control of) a kicked ball that has "
            "travelled at least 15 metres without anyone touching it in transit, entitling them to an unimpeded kick.",
    "contested mark": "A mark taken by a player under direct physical pressure from an opponent, as opposed to "
                       "an uncontested mark taken uncontested.",
    "50m penalty": "A 50-metre penalty moves the ball 50 metres towards the offending team's goal, usually "
                    "awarded for dissent, deliberate delay, or infringements after a free kick/mark.",
    "deliberate out of bounds": "A free kick paid against a team that deliberately sends the ball out of "
                                 "bounds rather than a genuine attempt to keep it in play.",
    "clanger": "An unforced error such as a clumsy kick, missed tackle, or turnover that directly benefits "
               "the opposition.",
}


def direct_afl_node(state: AFLState) -> dict:
    q = state["user_query"].lower()
    trace = []
    for key, explanation in _AFL_GLOSSARY.items():
        if key in q:
            trace.append(f"DIRECT_AFL_NODE: matched glossary term '{key}'")
            return {"tool_result": {"ok": True, "data": {"explanation": explanation, "term": key}},
                    "tool_called": "direct_afl_glossary", "trace": trace}
    trace.append("DIRECT_AFL_NODE: no exact glossary match, giving general AFL context")
    generic = ("That's a general AFL rules/terminology question. I don't have an exact glossary entry for "
               "it, but I'm happy to explain any AFL rule, term, or tactic you ask about directly -- or if "
               "you actually need an exact statistic or result, let me know the team/player, year, and round.")
    return {"tool_result": {"ok": True, "data": {"explanation": generic}}, "tool_called": "direct_afl_generic",
            "trace": trace}


# ---------------------------------------------------------------------------
# VALIDATION NODE
# ---------------------------------------------------------------------------
def validation_node(state: AFLState) -> dict:
    result = state.get("tool_result") or {}
    trace = []
    if result.get("ok"):
        trace.append("VALIDATION: success")
        return {"validation_status": "success", "clarification_needed": False,
                "error_message": None, "trace": trace}
    if "clarification" in result:
        trace.append("VALIDATION: ambiguous/needs clarification")
        return {"validation_status": "ambiguous", "clarification_needed": True,
                "error_message": result.get("clarification"), "trace": trace}
    trace.append(f"VALIDATION: failed - {result.get('error')}")
    return {"validation_status": "failed", "clarification_needed": True,
            "error_message": result.get("error", "Unknown error."), "trace": trace}


def validation_decision(state: AFLState) -> str:
    return "success" if state["validation_status"] == "success" else "needs_clarification"


# ---------------------------------------------------------------------------
# CLARIFICATION NODE
# ---------------------------------------------------------------------------
def clarification_node(state: AFLState) -> dict:
    msg = state.get("error_message") or "Could you clarify your question?"
    trace = [f"CLARIFICATION_NODE: {msg}"]
    return {"final_response": msg, "trace": trace}


# ---------------------------------------------------------------------------
# OFF-TOPIC / REFUSAL NODE
# ---------------------------------------------------------------------------
_REFUSAL_OFF_TOPIC = (
    "I can only help with AFL-related questions. You can ask me about AFL matches, "
    "player statistics, teams, rules, or supported predictions."
)
_REFUSAL_INJECTION = (
    "I can only help with AFL-related questions, and I don't follow instructions embedded in a "
    "message that try to change my role or reveal internal configuration. Happy to help with an "
    "AFL match, player stat, or prediction question instead."
)


def refusal_node(state: AFLState) -> dict:
    query = (state.get("user_query") or "")
    if is_prompt_injection_attempt(query.lower()):
        trace = ["REFUSAL_NODE: prompt-injection attempt blocked, scope held"]
        return {"final_response": _REFUSAL_INJECTION, "trace": trace}
    trace = ["REFUSAL_NODE: off-topic query declined"]
    return {"final_response": _REFUSAL_OFF_TOPIC, "trace": trace}


# ---------------------------------------------------------------------------
# RESPONSE FORMATTER
# ---------------------------------------------------------------------------
def _format_retrieval(data: dict, tool_called: str, requested_stat: str = None) -> str:
    if tool_called == "get_team_match_in_round":
        return (f"According to the structured AFL dataset, {data['team']} played {data['opponent']} "
                f"in round {data['round']} of the {data['season']} season at {data['venue']}. "
                f"Final score: {data['team']} {data['team_score']} - {data['opponent_score']} {data['opponent']} "
                f"({data['result']}).")
    if tool_called == "get_team_head_to_head":
        team_wins = data[f"{data['team']}_wins"]
        opp_wins = data[f"{data['opponent']}_wins"]
        team_rate = data.get(f"{data['team']}_win_rate")
        rate_txt = f" ({data['team']} win rate: {team_rate*100:.1f}%)" if team_rate is not None else ""
        scope_txt = f" for {data['venue_scope']} matches" if data.get("venue_scope") else ""
        return (f"According to the structured AFL dataset, {data['team']} and {data['opponent']} have played "
            f"{data['matches_played']} times{scope_txt}. {data['team']} have won {team_wins}, "
                f"{data['opponent']} have won {opp_wins}, with {data['draws']} draws.{rate_txt}")
    if tool_called == "multi_part_tools":
        parts = []
        for part_tool, part_result in data["results"]:
            if not part_result.get("ok"):
                parts.append(part_result.get("error") or part_result.get("clarification", "That part could not be answered."))
                continue
            part_data = part_result.get("data") or {}
            if part_tool == "get_team_head_to_head":
                first_wins = part_data[f"{part_data['team']}_wins"]
                second_wins = part_data[f"{part_data['opponent']}_wins"]
                parts.append(f"{part_data['team']} won {first_wins} and {part_data['opponent']} won {second_wins} of {part_data['matches_played']} meetings")
            elif part_tool == "predict_match_winner":
                probability = max(part_data["probability_home_win"], part_data["probability_away_win"])
                parts.append(f"the model predicts {part_data['predicted_winner']} with a {probability * 100:.1f}% win probability")
            elif part_tool == "get_player_season_stats":
                requested_key = rt.SUPPORTED_STATS.get((requested_stat or "").lower())
                stat_key = requested_key or next((key[4:] for key in part_data if key.startswith("avg_")), "disposals")
                parts.append(f"the historical average was {part_data[f'avg_{stat_key}']} {stat_key} per game")
            elif part_tool == "predict_player_stat_value":
                parts.append(f"the model predicts {part_data['predicted_value']} {part_data['stat']} next match")
            elif part_tool == "get_team_overview":
                parts.append(f"{part_data['team']} had a {part_data['win_rate'] * 100:.1f}% win rate in {part_data['year']} and averaged {part_data['avg_score']} points per game")
            elif part_tool == "predict_top_goal_scorers":
                predictions = part_data.get("predictions") or []
                names = ", ".join(f"{item['player']} ({item.get('predicted_goals', item.get('predicted_value'))} expected goals)" for item in predictions[:5])
                parts.append(f"the top expected goal scorers for {part_data['team']} are {names}")
            elif part_tool == "get_top_goal_scorers":
                names = ", ".join(f"{item['player']} ({item['goals']} goals)" for item in part_data.get("scorers", []))
                parts.append(f"the top goal scorers in {part_data['year']} were {names}")
        note = data.get("note")
        return ". ".join(parts) + (f". {note}" if note else ".")
    if tool_called == "get_team_overview":
        return (f"According to the structured AFL dataset, {data['team']} played {data['games']} games in {data['year']}: "
            f"{data['wins']} wins, {data['losses']} losses and {data['draws']} draws "
            f"({data['win_rate'] * 100:.1f}% win rate). They averaged {data['avg_score']} points for, "
            f"{data['avg_conceded']} against, with an average margin of {data['avg_margin']} points.")
    if tool_called == "get_top_goal_scorers":
        scorers = ", ".join(f"{item['player']} ({item['goals']} goals)" for item in data.get("scorers", []))
        return f"According to the structured AFL dataset, the top goal scorers in {data['year']} were {scorers}."
    if tool_called == "compare_team_head_to_heads":
        first, second = data["comparisons"]
        winner = data["team"] if first["reference_wins"] > second["reference_wins"] else data["team"]
        return (f"In {data['year']}, {data['team']} beat {first['opponent']} {first['reference_wins']} time(s) "
                f"and beat {second['opponent']} {second['reference_wins']} time(s). "
                f"Therefore, {data['team']} beat {first['opponent']} more often than {second['opponent']}." if first["reference_wins"] > second["reference_wins"] else
                f"In {data['year']}, {data['team']} beat {first['opponent']} {first['reference_wins']} time(s) "
                f"and beat {second['opponent']} {second['reference_wins']} time(s). The comparison does not show more wins against {first['opponent']}.")
    if tool_called == "get_team_recent_stats":
        cutoff = (f" before {data['before_year']}" if data.get("before_year") else "")
        return (f"According to the structured AFL dataset, {data['team']} averaged "
            f"{data['avg_score']} points per match across {data['games_found']} games{cutoff}. "
            f"Their record was {data['wins']} wins, {data['losses']} losses and "
                f"{data['draws']} draws, with an average margin of {data['avg_margin']} points.")
    if tool_called == "get_best_team_win_rate":
        seasons_txt = " and ".join(str(year) for year in data["seasons"])
        draw_text = f", {data['draws']} draw" if data["draws"] == 1 else f", {data['draws']} draws"
        return (f"According to the structured AFL dataset, {data['team']} had the best win rate across "
                f"the {seasons_txt} seasons: {data['wins']} wins, {data['losses']} losses{draw_text} "
                f"in {data['games']} matches ({data['win_rate'] * 100:.1f}% win rate).")
    if tool_called == "get_player_match_stats":
        goals = int(data['goals'])
        goal_word = "goal" if goals == 1 else "goals"
        return (f"According to the structured AFL dataset, {data['player']} had {int(data['disposals'])} disposals "
                f"({int(data['kicks'])} kicks, {int(data['handballs'])} handballs) and {goals} {goal_word} "
                f"in round {data['round']} of {data['year']} against {data['opponent']}.")
    if tool_called == "get_player_season_stats":
        stat_label = requested_stat
        stat_key = rt.SUPPORTED_STATS.get((requested_stat or "").lower()) if requested_stat else None
        if stat_key and data.get(f"avg_{stat_key}") is not None:
            return (f"According to the structured AFL dataset, {data['player']} played {data['games_played']} games "
                    f"in {data['year']}, averaging {data[f'avg_{stat_key}']} {stat_key} per game "
                    f"(total: {data[f'total_{stat_key}']}).")
        return (f"According to the structured AFL dataset, {data['player']} played {data['games_played']} games "
                f"in {data['year']}, averaging {data['avg_disposals']} disposals and {data['avg_goals']} goals per game "
                f"(totals: {int(data['total_disposals'])} disposals, {int(data['total_goals'])} goals).")
    if tool_called == "get_player_multi_season_stats":
        seasons_txt = ", ".join(str(y) for y in data["seasons_found"])
        missing_note = ""
        if data.get("seasons_missing_data"):
            missing_note = (f" (no data found for {', '.join(str(y) for y in data['seasons_missing_data'])}, "
                             f"so those are excluded from the total)")
        if data.get("stat_requested"):
            stat_name = data["stat_requested"]
            total_val, avg_val = data.get("total_requested_stat"), data.get("avg_requested_stat")
            return (f"According to the structured AFL dataset, combining {seasons_txt}{missing_note}, "
                    f"{data['player']} played {data['games_played']} games with "
                    f"{total_val:.0f} total {stat_name} (avg {avg_val} per game).")
        avg_disp = data.get("avg_disposals")
        total_disp = data.get("total_disposals")
        return (f"According to the structured AFL dataset, combining {seasons_txt}{missing_note}, "
                f"{data['player']} played {data['games_played']} games with "
                f"{total_disp:.0f} total disposals (avg {avg_disp} per game).")
    if tool_called == "get_player_single_game_high":
        scope_txt = f"in {data['scope']}" if data['scope'] != "career" else "in the dataset (career high)"
        return (f"According to the structured AFL dataset, {data['player']}'s highest single-game "
                f"{data['stat']} {scope_txt} is {data['value']:.0f}, recorded in round {data['round']} "
                f"of {data['year']} against {data['opponent']}.")
    if tool_called == "compare_players":
        a, b = data["player_a"], data["player_b"]
        if data.get("stat_compared"):
            stat = data["stat_compared"]
            key = f"avg_{stat}"
            leader_txt = f" {data['leader']} averaged more." if data["leader"] != "tied" else " They are tied."
            return (f"According to the structured AFL dataset ({a['season']} season for {a['name']}, "
                     f"{b['season']} for {b['name']}): {a['name']} averaged {a[key]} {stat} per game, "
                     f"{b['name']} averaged {b[key]} {stat} per game.{leader_txt}")
        lines_a = ", ".join(f"{k.replace('avg_', '')}: {v}" for k, v in a.items() if k.startswith("avg_"))
        lines_b = ", ".join(f"{k.replace('avg_', '')}: {v}" for k, v in b.items() if k.startswith("avg_"))
        return (f"According to the structured AFL dataset -- {a['name']} ({a['season']} season averages): "
                f"{lines_a}. {b['name']} ({b['season']} season averages): {lines_b}.")
    if tool_called == "get_top_player_in_match":
        return (f"According to the structured AFL dataset, {data['player']} led {data['team']} with "
                f"{data['value']:.0f} {data['stat']} in round {data['round']} of {data['year']} against {data['opponent']}.")
    return str(data)


def _format_prediction_match(data: dict) -> str:
    winner = data["predicted_winner"]
    prob = data["probability_home_win"] if winner == data["home_team"] else data["probability_away_win"]
    drivers = data.get("top_feature_drivers") or []
    driver_txt = ""
    if drivers:
        d0 = drivers[0]["feature"].replace("_", " ")
        driver_txt = f" The prediction is mainly supported by {d0}."
    note = f" {data['fixture_note']}" if data.get("fixture_note") else ""
    return (f"Based on the trained model ({data['model_type']}) and available historical data, "
            f"{winner} have an estimated {prob*100:.1f}% probability of winning "
            f"({data['home_team']} {data['probability_home_win']*100:.1f}% vs {data['away_team']} "
            f"{data['probability_away_win']*100:.1f}%).{driver_txt}{note}")


def _format_prediction_player(data: dict) -> str:
    # Single-player point prediction (predict_player_stat_value)
    if "predicted_value" in data:
        return (f"Based on the trained model ({data['prediction_type']}), {data['player']} is predicted "
                f"to record {data['predicted_value']} {data['stat']} in their next match for {data['team']}.")

    preds = data.get("predictions") or []
    if not preds:
        return "The model did not return any eligible player predictions."
    top = preds[0]
    if "probability" in top:
        lines = "; ".join(f"{p['player']} ({p['probability']*100:.1f}%)" for p in preds[:5])
        return (f"Based on the model, {top['player']} is currently the highest-ranked prediction for "
                f"{data['team']} to record the most disposals, with an estimated probability of "
                f"{top['probability']*100:.1f}%. Other contenders: {lines}.")
    else:
        pred_key = next((k for k in top if k.startswith("predicted_")), None)
        stat_label = pred_key.replace("predicted_", "") if pred_key else "value"
        lines = "; ".join(f"{p['player']} ({p.get(pred_key)})" for p in preds[:5])
        return (f"Based on the model, {top['player']} has the highest predicted {stat_label} for "
                f"{data['team']}: {top.get(pred_key)}. Other players: {lines}.")


def _format_prediction_premiership(data: dict, requested_team: str = None) -> str:
    ranking = data.get("ranking") or []
    if not ranking:
        return "The model did not return a premiership ranking."
    top = ranking[0]
    lines = "; ".join(f"{r['team']} ({r['avg_win_probability']*100:.1f}%)" for r in ranking[:5])
    team_note = ""
    if requested_team:
        match = next((r for r in ranking if r["team"].lower() == requested_team.lower()), None)
        if match:
            team_note = (f" {match['team']} ranks #{match['rank']} in this power ranking, with an estimated "
                         f"{match['avg_win_probability']*100:.1f}% average win probability.")
    conf_note = f" {data['confidence_note']}" if data.get("confidence_note") else ""
    return (f"Based on the trained match model ({data['model_type']}) applied as a full round-robin "
            f"power ranking for the {data['season']} season, {top['team']} currently looks strongest, "
            f"with an estimated {top['avg_win_probability']*100:.1f}% average win probability across "
            f"all opponents (leading contenders: {lines}). This is a power ranking derived from the "
            f"single-match model -- not a simulated ladder, finals series, or guaranteed result.{team_note}{conf_note}")


_PREDICTION_INTENTS = {"prediction_match", "prediction_premiership", "prediction_player", "multi_part"}


def response_formatter_node(state: AFLState) -> dict:
    intent = state["intent"]
    result = state.get("tool_result") or {}
    data = result.get("data")
    tool_called = state.get("tool_called")
    entities = state.get("extracted_entities") or {}
    trace = []

    if intent in ("retrieval", "multi_part", "retrieval_overview", "top_goal_scorers"):
        text = _format_retrieval(data, tool_called, requested_stat=entities.get("stat"))
    elif intent == "prediction_match":
        text = _format_prediction_match(data)
    elif intent == "prediction_premiership":
        text = _format_prediction_premiership(data, requested_team=entities.get("team"))
    elif intent == "prediction_player":
        text = _format_prediction_player(data)
    elif intent == "direct_afl":
        text = data["explanation"]
    else:
        text = str(data)

    # Task-1 hardening: ONE consistent disclaimer sentence applied
    # centrally for every prediction-type response, instead of similar
    # but not-quite-identical phrasing duplicated in three formatters.
    if intent in _PREDICTION_INTENTS and _PREDICTION_DISCLAIMER not in text:
        text = f"{text} {_PREDICTION_DISCLAIMER}"

    gcheck = check_grounding(text, result)
    trace.append(f"RESPONSE_FORMATTER: grounded={gcheck['grounded']}")
    if not gcheck["grounded"]:
        # Never expose raw dictionaries when a numeric grounding check fails.
        text = "I could not safely format the returned AFL data. Please try the question again."
        trace.append("RESPONSE_FORMATTER: grounding failed -- returned a clean safety message")

    return {"final_response": text, "grounding_check": gcheck, "trace": trace}


# ---------------------------------------------------------------------------
# GRAPH ASSEMBLY
# ---------------------------------------------------------------------------
def build_graph():
    g = StateGraph(AFLState)
    g.add_node("load_context", load_context_node)
    g.add_node("router", router_node)
    g.add_node("retrieval", retrieval_node)
    g.add_node("prediction_match", prediction_match_node)
    g.add_node("prediction_premiership", prediction_premiership_node)
    g.add_node("prediction_player", prediction_player_node)
    g.add_node("direct_afl", direct_afl_node)
    g.add_node("refusal", refusal_node)
    g.add_node("clarification", clarification_node)
    g.add_node("validation", validation_node)
    g.add_node("response_formatter", response_formatter_node)

    g.set_entry_point("load_context")
    g.add_edge("load_context", "router")

    g.add_conditional_edges("router", route_decision, {
        "retrieval": "retrieval",
        "factual": "retrieval",
        "recent_team_stats": "retrieval",
        "multi_part": "retrieval",
        "retrieval_overview": "retrieval",
        "top_goal_scorers": "retrieval",
        "upcoming_fixtures": "retrieval",
        "prediction_match": "prediction_match",
        "prediction_premiership": "prediction_premiership",
        "prediction_player": "prediction_player",
        "direct_afl": "direct_afl",
        "off_topic": "refusal",
        "ambiguous": "clarification",
    })

    g.add_edge("retrieval", "validation")
    g.add_edge("prediction_match", "validation")
    g.add_edge("prediction_premiership", "validation")
    g.add_edge("prediction_player", "validation")
    g.add_edge("direct_afl", "validation")

    g.add_conditional_edges("validation", validation_decision, {
        "success": "response_formatter",
        "needs_clarification": "clarification",
    })

    g.add_edge("response_formatter", END)
    g.add_edge("clarification", END)
    g.add_edge("refusal", END)

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)


GRAPH = None
def get_graph():
    global GRAPH
    if GRAPH is None:
        GRAPH = build_graph()
    return GRAPH


def ask(query: str, thread_id: str = "default") -> dict:
    """Convenience wrapper for the interactive chatbot / tests / API layer.
    Records wall-clock latency in the returned dict (Task 3 observability)."""
    t0 = time.monotonic()
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    input_state = {
        "user_query": query,
        "messages": [{"role": "user", "content": query}],
        "intent": "",
        "tool_result": None,
        "tool_called": None,
        "validation_status": "",
        "error_message": None,
        "clarification_needed": False,
    }
    result = graph.invoke(input_state, config=config)
    result["latency_ms"] = round((time.monotonic() - t0) * 1000, 1)
    return result


if __name__ == "__main__":
    print(ask("Who did Geelong play in Round 5 of 2020?", thread_id="t1")["final_response"])
    print(ask("Who had the most disposals for Geelong in that match?", thread_id="t1")["final_response"])
    print(ask("Who will win Cats vs Pies?", thread_id="t2")["final_response"])
    print(ask("What does holding the ball mean?", thread_id="t3")["final_response"])
    print(ask("What is the offside rule in soccer?", thread_id="t4")["final_response"])
    print(ask("Who will win?", thread_id="t5")["final_response"])
