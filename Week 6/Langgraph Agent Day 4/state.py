"""
state.py
========
Section 10: LangGraph State schema.

TypedDict-based state, kept flat and explicit per the assignment brief.
`entities` carries resolved conversational context (team/opponent/player/
year/round/stat/prediction_type) forward across turns so follow-up
questions ("who had the most disposals?", "what about the round before
that?") can be answered without the user repeating themselves.
"""
from __future__ import annotations
from typing import TypedDict, Optional, List, Dict, Any
import operator
from typing_extensions import Annotated


class Entities(TypedDict, total=False):
    team: Optional[str]
    opponent: Optional[str]
    player: Optional[str]
    year: Optional[int]
    round: Optional[str]
    stat: Optional[str]
    prediction_type: Optional[str]
    home_team: Optional[str]
    away_team: Optional[str]


class AFLState(TypedDict, total=False):
    # -- input / conversation -------------------------------------------------
    user_query: str
    messages: Annotated[List[Dict[str, str]], operator.add]  # [{"role":..,"content":..}, ...]

    # -- routing ---------------------------------------------------------------
    intent: str  # factual | retrieval | prediction_match | prediction_premiership | prediction_player | direct_afl | off_topic | ambiguous

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

    # -- tracing ------------------------------------------------------------------
    trace: Annotated[List[str], operator.add]
