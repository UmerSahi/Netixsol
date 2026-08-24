"""
state.py
========
Section 10: LangGraph State schema.

TypedDict-based state, kept flat and explicit per the assignment brief.
`entities` carries resolved conversational context (team/opponent/player/
year/round/stat/prediction_type) forward across turns so follow-up
questions ("who had the most disposals?", "what about the round before
that?") can be answered without the user repeating themselves.

CAPSTONE ADDITIONS (v2):
    - player2: second player for head-to-head player comparisons
      ("Sam Walsh vs Lachie Neale disposals").
    - years: a list of ints for multi-season combined-stat queries
      ("tackles across 2022 and 2023 combined"), kept separate from the
      single scalar `year` so single-season lookups are unaffected.
    - team_ambiguous_candidates / player_ambiguous_candidates: transient,
      never persisted across turns (see graph.router_node), used only to
      pass an unresolved-ambiguity list into the node that needs to ask
      for clarification.
"""
from __future__ import annotations
from typing import TypedDict, Optional, List, Dict, Any
import operator
from typing_extensions import Annotated


class Entities(TypedDict, total=False):
    team: Optional[str]
    opponent: Optional[str]
    player: Optional[str]
    player2: Optional[str]
    year: Optional[int]
    before_year: Optional[int]
    years: Optional[List[int]]
    round: Optional[str]
    stat: Optional[str]
    prediction_type: Optional[str]
    home_team: Optional[str]
    away_team: Optional[str]
    venue_scope: Optional[str]
    overview_year: Optional[int]
    top_n: Optional[int]
    team_ambiguous_candidates: Optional[List[str]]
    player_ambiguous_candidates: Optional[List[str]]


class AFLState(TypedDict, total=False):
    # -- input / conversation -------------------------------------------------
    user_query: str
    messages: Annotated[List[Dict[str, str]], operator.add]  # [{"role":..,"content":..}, ...]

    # -- routing ---------------------------------------------------------------
    intent: str  # factual | retrieval | retrieval_overview | top_goal_scorers | upcoming_fixtures | recent_team_stats | prediction_match | prediction_premiership | prediction_player | multi_part | direct_afl | off_topic | ambiguous
    multi_results: Optional[List[Dict[str, Any]]]

    # -- entities / context ------------------------------------------------
    extracted_entities: Entities

    # -- tool execution ------------------------------------------------------
    tool_result: Optional[Dict[str, Any]]
    tool_called: Optional[str]

    # -- validation -----------------------------------------------------------
    validation_status: str  # success | failed | ambiguous
    error_message: Optional[str]
    clarification_needed: bool
    retry_count: int

    # -- output -----------------------------------------------------------------
    final_response: str

    # -- grounding --------------------------------------------------------------
    grounding_check: Optional[Dict[str, Any]]

    # -- observability (Task 1/3 hardening) --------------------------------------
    latency_ms: Optional[float]
    router_source: Optional[str]  # "gemini" | "rule_based" -- which classifier actually answered

    # -- tracing ------------------------------------------------------------------
    trace: Annotated[List[str], operator.add]
