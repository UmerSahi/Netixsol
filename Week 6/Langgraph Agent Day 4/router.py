"""
router.py
=========
Section 11 (+ Section 2 design): Router node.

Classifies user_query into one of:
    factual | retrieval | prediction_match | prediction_player |
    direct_afl | off_topic | ambiguous

and extracts entities (team, opponent, player, year, round, stat,
prediction_type), carrying forward unresolved context from the previous
turn's `extracted_entities` so follow-up questions work.

Two classification paths:
  1. LLM path (Gemini via langchain_google_genai) with a Pydantic structured
     output schema -- used when GOOGLE_API_KEY is set.
  2. Deterministic rule-based fallback -- always available, used when no API
     key is configured OR when the LLM's output fails Pydantic validation.
     This guarantees the router NEVER crashes or silently defaults to a
     wrong intent just because the LLM call failed or returned malformed
     JSON.

Both paths return the same validated Pydantic object, so every downstream
node only ever sees one shape of router output.
"""
from __future__ import annotations
import os
import re
from typing import Optional, Literal
from pydantic import BaseModel, Field, ValidationError
from data_layer import get_known_teams
from resolvers import _ALIAS_MAP

INTENTS = ["factual", "retrieval", "prediction_match", "prediction_premiership", "prediction_player",
           "direct_afl", "off_topic", "ambiguous"]


class RouterOutput(BaseModel):
    intent: Literal["factual", "retrieval", "prediction_match", "prediction_premiership",
                     "prediction_player", "direct_afl", "off_topic", "ambiguous"]
    team: Optional[str] = None
    opponent: Optional[str] = None
    player: Optional[str] = None
    year: Optional[int] = None
    round: Optional[str] = None
    stat: Optional[str] = None
    prediction_type: Optional[str] = None
    reasoning: Optional[str] = Field(default=None, description="one short sentence")


# ---------------------------------------------------------------------------
# Deterministic fallback classifier (also the ONLY classifier when no
# GOOGLE_API_KEY is configured, e.g. this sandboxed evaluation environment)
# ---------------------------------------------------------------------------
_OFF_TOPIC_MARKERS = [
    "soccer", "football offside", "nba", "basketball", "nfl", "cricket score",
    "recipe", "pasta", "quantum physics", "quantum", "stock market", "weather",
    "python code", "javascript", "movie", "election", "tv show",
]
_AFL_RULE_MARKERS = ["holding the ball", "holding the man", "what is a mark", "contested mark",
                     "free kick rule", "afl rule", "afl rules", "how does afl", "what does",
                     "what is a", "explain", "rule", "term", "terminology", "50m penalty",
                     "50 metre", "deliberate out of bounds"]
_PREDICTION_MATCH_MARKERS = ["who will win", "will win", "beat", "predict", "prediction",
                             "who wins", "chances of winning", "favourite", "favorite",
                             "odds of"]
_PREMIERSHIP_MARKERS = ["premiership", "the flag", "win the flag", "championship",
                         "grand final winner", "win the competition", "win the season",
                         "win the league"]
_PREDICTION_PLAYER_MARKERS = ["most likely", "who will top", "top-score", "top score",
                              "predicted to", "expected disposals", "expected to get",
                              "who is likely"]
_RETRIEVAL_MARKERS = ["how many", "what was the score", "who did", "who played", "which team",
                      "team did", "did they play", "disposals did", "goals did", "most disposals",
                      "most goals", "head to head", "head-to-head", "record against", "score",
                      "who won", "average", "season total", "season average",
                      "grand final", "stats", "statistics"]
_STAT_WORDS = list(__import__("retrieval_tools").SUPPORTED_STATS.keys())


def _is_premiership_query(ql: str) -> bool:
    """Season/league-wide prediction request ("who will win the AFL in
    2030?", "premiership favourite?") as opposed to a single named
    matchup. Checked ahead of the two-team prediction_match check so a
    league-wide question is never mistaken for (or forced to need) a
    specific matchup."""
    if any(m in ql for m in _PREMIERSHIP_MARKERS):
        return True
    # "who will win the AFL [in <year>]" / "win AFL this year" phrasing --
    # distinguished from a two-team matchup by the literal word "afl" (or
    # "competition"/"season"/"league") standing in for "the whole thing"
    # rather than a specific opponent.
    if re.search(r"\bwin\b", ql) and re.search(r"\bafl\b|\bcompetition\b|\bleague\b", ql):
        return True
    return False


def _count_team_mentions(ql: str) -> int:
    """Count distinct teams referenced in a (lowercased) query, via full
    canonical name, significant name word, or known alias token. Used to
    distinguish a genuine two-team prediction request ('Cats vs Pies') from
    a vague one ('who will win?') where the trigger word 'win' alone must
    not be mistaken for two named teams."""
    mentioned = set()
    for team in get_known_teams():
        words = team.lower().split()
        for w in [team.lower()] + [w for w in words if len(w) > 3]:
            if re.search(rf"\b{re.escape(w)}\b", ql):
                mentioned.add(team)
                break
    for alias, target in _ALIAS_MAP.items():
        if re.search(rf"\b{re.escape(alias)}\b", ql):
            mentioned.add(target)
    return len(mentioned)


def _extract_year(q: str) -> Optional[int]:
    # Any 1900s (from 1950) or 2000s/2100s year -- broad enough to cover
    # both historical data (back to 1983) and future/hypothetical queries
    # ("AFL in 2030") without an arbitrary upper cutoff.
    m = re.search(r"\b(19[5-9]\d|20\d\d|21\d\d)\b", q)
    return int(m.group(1)) if m else None


def _extract_round(q: str) -> Optional[str]:
    m = re.search(r"\bround\s*(\d{1,2}|EF|QF|SF|PF|GF)\b", q, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"\b(grand final)\b", q, re.IGNORECASE)
    if m2:
        return "GF"
    return None


def _extract_stat(q: str) -> Optional[str]:
    ql = q.lower()
    for s in _STAT_WORDS:
        if s in ql:
            return s
    return None


def _extract_relative_round(q: str, prior_round) -> Optional[str]:
    """Resolve phrases like 'the round before that' / 'the previous round'
    relative to the prior turn's round, ONLY when that prior round is a
    plain integer (numeric rounds can be decremented unambiguously; finals
    codes like 'GF'/'QF' cannot, so we deliberately do not guess there)."""
    ql = q.lower()
    if prior_round is None:
        return None
    try:
        prior_int = int(prior_round)
    except (TypeError, ValueError):
        return None
    if re.search(r"\b(round before|previous round|prior round|round prior)\b", ql):
        return str(prior_int - 1) if prior_int > 1 else None
    if re.search(r"\b(round after|next round|following round)\b", ql):
        return str(prior_int + 1)
    return None


def rule_based_route(query: str, prior_entities: dict = None) -> RouterOutput:
    """Deterministic keyword/regex classifier used as the default router
    (no external API dependency) and as the safety-net fallback for the LLM
    path. Order of checks matters: prediction/retrieval markers are checked
    before the more generic direct_afl/off_topic markers."""
    q = query.strip()
    ql = q.lower()
    prior_entities = prior_entities or {}

    year = _extract_year(ql) or prior_entities.get("year")
    round_ = _extract_round(ql)
    stat = _extract_stat(ql) or prior_entities.get("stat")

    if not q:
        return RouterOutput(intent="ambiguous", reasoning="Empty query.")

    # Relative round follow-ups ("what about the round before that?") --
    # resolved deterministically when unambiguous, routed straight to
    # retrieval since the team/player context carries over from prior_entities.
    rel_round = _extract_relative_round(ql, prior_entities.get("round"))
    if rel_round is not None:
        return RouterOutput(intent="retrieval", year=year, round=rel_round, stat=stat,
                             reasoning="Resolved a relative round reference from prior context.")
    if round_ is None and re.search(r"\b(round before|previous round|prior round|round after|next round)\b", ql):
        # relative round requested but prior round unknown/non-numeric -> don't guess
        return RouterOutput(intent="ambiguous", year=year,
                             reasoning="Relative round requested but the prior round isn't a resolvable number.")
    round_ = round_ or prior_entities.get("round")

    if any(m in ql for m in _OFF_TOPIC_MARKERS):
        return RouterOutput(intent="off_topic", reasoning="Matched an off-topic marker.")

    if _is_premiership_query(ql):
        return RouterOutput(intent="prediction_premiership", year=year,
                             reasoning="Matched a season/league-wide prediction request (not a single named matchup).")

    if any(m in ql for m in _PREDICTION_MATCH_MARKERS) and _count_team_mentions(ql) >= 2:
        return RouterOutput(intent="prediction_match", year=year, round=round_,
                             reasoning="Matched a match-prediction marker with two teams named.")

    if any(m in ql for m in _PREDICTION_PLAYER_MARKERS):
        return RouterOutput(intent="prediction_player", year=year, round=round_, stat=stat,
                             reasoning="Matched a player-prediction marker.")

    if ("predict" in ql or "who will" in ql) and ("top player" in ql or "top-score" in ql or "top scorer" in ql):
        return RouterOutput(intent="prediction_player", year=year, round=round_, stat=stat,
                             reasoning="Matched a 'predict top player' pattern.")

    # Broader player-prediction catch-all: "predict"/"who will be" combined
    # with "player" covers requests for prediction TYPES our models don't
    # support (e.g. "best defender") -- these still route to
    # prediction_player so the node/tool can give the correct "unsupported"
    # explanation instead of a generic clarification.
    if ("predict" in ql or "who will" in ql or "who is likely" in ql) and "player" in ql:
        unsupported_type = None
        for kw in ["defender", "tackler", "best on ground", "brownlow", "mvp"]:
            if kw in ql:
                unsupported_type = kw.replace(" ", "_")
        return RouterOutput(intent="prediction_player", year=year, round=round_, stat=stat,
                             prediction_type=unsupported_type,
                             reasoning="Matched a general player-prediction request.")

    if any(m in ql for m in _RETRIEVAL_MARKERS):
        return RouterOutput(intent="retrieval", year=year, round=round_, stat=stat,
                             reasoning="Matched a retrieval marker (factual number/result request).")

    # A stat word mentioned with an already-resolved player/team in context
    # ("What were his disposals and goals?") is a retrieval follow-up even
    # without one of the fixed marker phrases above.
    if stat and (prior_entities.get("player") or prior_entities.get("team")):
        return RouterOutput(intent="retrieval", year=year, round=round_, stat=stat,
                             reasoning="Stat word mentioned with resolved player/team context from prior turn.")

    if any(m in ql for m in _AFL_RULE_MARKERS):
        return RouterOutput(intent="direct_afl", reasoning="Matched a direct AFL explanation marker.")

    # generic pronoun-only follow-ups referring to unresolved prior context
    if re.match(r"^(who|what|and|what about|how about)\b", ql) and len(ql.split()) <= 6:
        return RouterOutput(intent="ambiguous", year=year, round=round_,
                             reasoning="Short follow-up without enough new information.")

    if "best sport" in ql or "greatest sport" in ql:
        return RouterOutput(intent="ambiguous", reasoning="Opinion question not scoped to AFL retrieval/prediction.")

    return RouterOutput(intent="ambiguous", year=year, round=round_,
                         reasoning="No confident pattern match; asking for clarification is safer than guessing.")


# ---------------------------------------------------------------------------
# LLM path (Gemini) -- only used if GOOGLE_API_KEY is present.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """You are the router for an AFL (Australian Football League) AI agent.
Classify the user's query into exactly one intent:
- factual: a general known AFL fact not requiring dataset lookup (rare -- prefer retrieval/direct_afl)
- retrieval: requires an EXACT number/result/score/statistic from historical data
- prediction_match: asks who will win / beat / probability of winning a future or hypothetical matchup
- prediction_player: asks who is likely to lead in a stat / top-score / expected value for a player
- direct_afl: general AFL rules/terminology explanation, no exact numbers needed
- off_topic: not about AFL at all
- ambiguous: not enough information, or a vague/opinion question

Also extract any of these entities mentioned or implied by conversation history:
team, opponent, player, year, round, stat, prediction_type.
If the user refers back to something from the conversation ("that match", "in that game",
"the round before"), resolve it using the provided prior entities ONLY if unambiguous;
otherwise leave the field blank and prefer intent=ambiguous.
Never invent a team, player, year, or round that was not stated or clearly implied."""


_GEMINI_MODEL = "gemini-3.5-flash-lite"  # single source of truth for the model string used everywhere


def llm_route(query: str, history: list, prior_entities: dict) -> Optional[RouterOutput]:
    """Attempt to classify using Gemini with structured output. Returns None
    (never raises) if no API key is configured or the call/parse fails --
    callers must fall back to rule_based_route in that case.

    Set AFL_ROUTER_DEBUG=1 to print the real exception on failure instead of
    silently falling back -- useful when diagnosing why the LLM path isn't
    being used."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        # Note: gemini-3.5-flash-lite uses fixed sampling defaults and
        # ignores an explicit temperature, so it's intentionally omitted
        # here (passing it just produces a harmless UserWarning).
        llm = ChatGoogleGenerativeAI(model=_GEMINI_MODEL, google_api_key=api_key)
        structured_llm = llm.with_structured_output(RouterOutput)
        history_text = "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])
        prompt = (f"{_SYSTEM_PROMPT}\n\nConversation so far:\n{history_text}\n\n"
                  f"Prior resolved entities: {prior_entities}\n\nCurrent user message: {query}")
        result = structured_llm.invoke(prompt)
        if isinstance(result, RouterOutput):
            return result
        return RouterOutput(**result) if isinstance(result, dict) else None
    except Exception as e:
        if os.environ.get("AFL_ROUTER_DEBUG"):
            import traceback
            print(f"[router.llm_route] Gemini path failed, falling back to rule-based router: {e}")
            traceback.print_exc()
        return None


def route_query(query: str, history: list = None, prior_entities: dict = None) -> RouterOutput:
    """Public entry point used by the router node. Tries the LLM path first
    (if configured), falls back to the deterministic classifier on any
    failure or invalid output -- the router is guaranteed to always return
    a valid RouterOutput."""
    history = history or []
    prior_entities = prior_entities or {}
    llm_result = llm_route(query, history, prior_entities)
    if llm_result is not None:
        return llm_result
    return rule_based_route(query, prior_entities)


if __name__ == "__main__":
    tests = [
        "Who did Geelong play in Round 5 of 2020?",
        "What was Patrick Dangerfield's average disposals in 2020?",
        "Who had the most disposals for Geelong in Round 5?",
        "Who will win Cats vs Pies?",
        "Will the Pies beat the Cats this week?",
        "Who is most likely to have the most disposals?",
        "Who will top-score for Geelong?",
        "What does holding the ball mean?",
        "What is the offside rule in soccer?",
        "What's the best sport?",
    ]
    for t in tests:
        print(t, "->", route_query(t).intent)