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
"""
from __future__ import annotations
import os
from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from state import AFLState
from router import route_query
from resolvers import resolve_team, resolve_player
from entity_extraction import find_team_candidates, find_player_candidate
from router import _extract_year, _extract_round, _extract_stat
import retrieval_tools as rt
import prediction_tools as pt
from grounding import check_grounding

MAX_RETRIES = 1  # no infinite clarification loops


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
def router_node(state: AFLState) -> dict:
    query = state["user_query"]
    history = state.get("messages", [])
    prior_entities = state.get("extracted_entities") or {}

    result = route_query(query, history, prior_entities)

    # merge new entities over prior ones (new info wins, but we keep
    # anything the LLM/rule router didn't re-specify this turn)
    merged = dict(prior_entities)
    for field in ["team", "opponent", "player", "year", "round", "stat", "prediction_type"]:
        val = getattr(result, field, None)
        if val is not None:
            merged[field] = val

    # Best-effort supplementary extraction for the rule-based path: pull
    # team/player candidate substrings so retrieval/prediction nodes have
    # something to resolve even if the router itself didn't set them.
    # IMPORTANT: when a single ambiguous word matches multiple teams (e.g.
    # "Coast" -> Gold Coast Suns / West Coast Eagles) and the query is NOT
    # clearly a two-team matchup, we must NOT silently pick cands[0] -- that
    # would bypass resolve_team's own ambiguity handling entirely. Instead
    # we flag it so the retrieval/prediction node can surface a genuine
    # clarification.
    has_matchup_word = any(w in query.lower() for w in [" vs ", " v ", "beat", " against "])
    if not merged.get("team"):
        cands = find_team_candidates(query)
        if len(cands) == 1:
            merged["team"] = cands[0]
        elif len(cands) >= 2 and has_matchup_word:
            merged["team"] = cands[0]
            merged["opponent"] = cands[1]
        elif len(cands) >= 2:
            merged["team_ambiguous_candidates"] = cands
    elif not merged.get("opponent"):
        cands = find_team_candidates(query)
        others = [c for c in cands if c.lower() != str(merged.get("team", "")).lower()]
        if others:
            merged["opponent"] = others[0]

    if not merged.get("player"):
        cand = find_player_candidate(query)
        if cand:
            merged["player"] = cand

    trace = [f"ROUTER: intent={result.intent} entities={merged} reasoning={result.reasoning}"]
    return {"intent": result.intent, "extracted_entities": merged, "trace": trace}


def route_decision(state: AFLState) -> str:
    return state["intent"]


# ---------------------------------------------------------------------------
# RETRIEVAL NODE
# ---------------------------------------------------------------------------
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

    # Decide which retrieval tool this query needs based on the entities we have.
    if e.get("player") and e.get("year") and ("season average" in q or "season total" in q or
                                                "season stats" in q or ("season" in q and "average" in q)):
        # Explicit season-level request always wins over any stale round/stat
        # carried forward from an earlier turn in the same thread.
        tool_name, result = "get_player_season_stats", rt.get_player_season_stats(e["player"], e["year"])
    elif e.get("player") and not e.get("stat") and ("season" in q or "average" in q or "total" in q) and e.get("year") and "round" not in q and not e.get("round"):
        tool_name, result = "get_player_season_stats", rt.get_player_season_stats(e["player"], e["year"])
    elif e.get("player") and e.get("year") and e.get("round"):
        tool_name, result = "get_player_match_stats", rt.get_player_match_stats(e["player"], e["year"], e["round"])
    elif e.get("stat") and e.get("team") and e.get("year") and e.get("round"):
        tool_name, result = "get_top_player_in_match", rt.get_top_player_in_match(e["team"], e["year"], e["round"], e["stat"])
    elif ("most disposals" in q or "most goals" in q or "top player" in q) and e.get("team") and e.get("year") and e.get("round"):
        stat = e.get("stat") or ("goals" if "goals" in q else "disposals")
        tool_name, result = "get_top_player_in_match", rt.get_top_player_in_match(e["team"], e["year"], e["round"], stat)
    elif e.get("team") and e.get("opponent") and ("head" in q or "record" in q or "history" in q):
        tool_name, result = "get_team_head_to_head", rt.get_team_head_to_head(e["team"], e["opponent"])
    elif e.get("team") and e.get("year") and e.get("round"):
        tool_name, result = "get_team_match_in_round", rt.get_team_match_in_round(e["team"], e["year"], e["round"])
    elif e.get("player") and e.get("year"):
        tool_name, result = "get_player_season_stats", rt.get_player_season_stats(e["player"], e["year"])
    elif e.get("team") and e.get("opponent"):
        tool_name, result = "get_team_head_to_head", rt.get_team_head_to_head(e["team"], e["opponent"])
    else:
        missing = []
        if not e.get("team") and not e.get("player"):
            missing.append("a team or player")
        if not e.get("year"):
            missing.append("a year/season")
        if not e.get("round") and not ("season" in q or "average" in q):
            missing.append("a round")
        tool_name, result = None, {
            "ok": False,
            "clarification": f"I need {', and '.join(missing) if missing else 'more detail'} to look that up exactly. Could you provide it?"
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

    result = pt.predict_match_winner(home, away, fixture_confirmed=False)
    trace.append(f"PREDICTION_MATCH_NODE: home={home} away={away} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_match_winner", "trace": trace}


def prediction_premiership_node(state: AFLState) -> dict:
    """Season/league-wide 'who will win the AFL/premiership/flag [in <year>]'
    queries -- routed separately from prediction_match_node since there is
    no single named matchup, only (optionally) a target season."""
    e = state.get("extracted_entities") or {}
    trace = []
    result = pt.predict_premiership_favourite(season=e.get("year"))
    trace.append(f"PREDICTION_PREMIERSHIP_NODE: season={e.get('year')} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_premiership_favourite", "trace": trace}


def prediction_player_node(state: AFLState) -> dict:
    e = state.get("extracted_entities") or {}
    team = e.get("team")
    opponent = e.get("opponent")
    pred_type = e.get("prediction_type") or "top_disposals"
    q = (state["user_query"] or "").lower()
    if "goal" in q:
        pred_type = "unsupported_top_goals"  # no trained model for this -- see SUPPORTED_PLAYER_PREDICTIONS
    elif "disposal" in q and ("expected" in q or "predicted disposals" in q):
        pred_type = "expected_disposals"
    trace = []
    if not team:
        result = {"ok": False, "clarification": "Which team's players should I predict for?"}
        trace.append("PREDICTION_PLAYER_NODE: missing team")
        return {"tool_result": result, "tool_called": "predict_top_player", "trace": trace}

    result = pt.predict_top_player(team, opponent=opponent, prediction_type=pred_type)
    trace.append(f"PREDICTION_PLAYER_NODE: team={team} type={pred_type} ok={result.get('ok')}")
    return {"tool_result": result, "tool_called": "predict_top_player", "trace": trace}


# ---------------------------------------------------------------------------
# DIRECT AFL NODE
# ---------------------------------------------------------------------------
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
_REFUSALS = [
    "I can only help with AFL-related questions. You can ask me about AFL matches, "
    "player statistics, teams, rules, or supported predictions.",
    "That is outside my AFL scope. I can help you check an AFL match result, compare "
    "teams, or make a model-based prediction.",
]


def refusal_node(state: AFLState) -> dict:
    trace = ["REFUSAL_NODE: off-topic query declined"]
    return {"final_response": _REFUSALS[0], "trace": trace}


# ---------------------------------------------------------------------------
# RESPONSE FORMATTER
# ---------------------------------------------------------------------------
def _format_retrieval(data: dict, tool_called: str) -> str:
    if tool_called == "get_team_match_in_round":
        return (f"According to the structured AFL dataset, {data['team']} played {data['opponent']} "
                f"in round {data['round']} of the {data['season']} season at {data['venue']}. "
                f"Final score: {data['team']} {data['team_score']} - {data['opponent_score']} {data['opponent']} "
                f"({data['result']}).")
    if tool_called == "get_team_head_to_head":
        team_wins = data[f"{data['team']}_wins"]
        opp_wins = data[f"{data['opponent']}_wins"]
        return (f"According to the structured AFL dataset, {data['team']} and {data['opponent']} have played "
                f"{data['matches_played']} times. {data['team']} have won {team_wins}, "
                f"{data['opponent']} have won {opp_wins}, with {data['draws']} draws.")
    if tool_called == "get_player_match_stats":
        goals = int(data['goals'])
        goal_word = "goal" if goals == 1 else "goals"
        return (f"According to the structured AFL dataset, {data['player']} had {int(data['disposals'])} disposals "
                f"({int(data['kicks'])} kicks, {int(data['handballs'])} handballs) and {goals} {goal_word} "
                f"in round {data['round']} of {data['year']} against {data['opponent']}.")
    if tool_called == "get_player_season_stats":
        return (f"According to the structured AFL dataset, {data['player']} played {data['games_played']} games "
                f"in {data['year']}, averaging {data['avg_disposals']} disposals and {data['avg_goals']} goals per game "
                f"(totals: {int(data['total_disposals'])} disposals, {int(data['total_goals'])} goals).")
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
            f"{data['probability_away_win']*100:.1f}%).{driver_txt} "
            f"This is a model estimate, not a guaranteed result.{note}")


def _format_prediction_player(data: dict) -> str:
    preds = data.get("predictions") or []
    if not preds:
        return "The model did not return any eligible player predictions."
    top = preds[0]
    if "probability" in top:
        lines = "; ".join(f"{p['player']} ({p['probability']*100:.1f}%)" for p in preds[:5])
        return (f"Based on the model, {top['player']} is currently the highest-ranked prediction for "
                f"{data['team']} to record the most disposals, with an estimated probability of "
                f"{top['probability']*100:.1f}%. This is probabilistic, not certain. "
                f"Other contenders: {lines}.")
    else:
        lines = "; ".join(f"{p['player']} ({p['predicted_disposals']})" for p in preds[:5])
        return (f"Based on the model, {top['player']} has the highest predicted disposal count for "
                f"{data['team']}: predicted disposals: {top['predicted_disposals']}. This is a regression "
                f"estimate, not a probability. Other players: {lines}.")


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


def response_formatter_node(state: AFLState) -> dict:
    intent = state["intent"]
    result = state.get("tool_result") or {}
    data = result.get("data")
    tool_called = state.get("tool_called")
    entities = state.get("extracted_entities") or {}
    trace = []

    if intent == "retrieval":
        text = _format_retrieval(data, tool_called)
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

    gcheck = check_grounding(text, result)
    trace.append(f"RESPONSE_FORMATTER: grounded={gcheck['grounded']}")
    if not gcheck["grounded"]:
        # Self-correction: if a number in the formatted text can't be traced
        # back to the tool result, fall back to a conservative templated
        # dump of the tool result rather than risk presenting an invented figure.
        text = f"Here is the exact data returned by the tool: {data}"
        trace.append("RESPONSE_FORMATTER: grounding failed -- fell back to raw tool data")

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
    """Convenience wrapper for the interactive chatbot / tests."""
    graph = get_graph()
    config = {"configurable": {"thread_id": thread_id}}
    input_state = {
        "user_query": query,
        "messages": [{"role": "user", "content": query}],
    }
    result = graph.invoke(input_state, config=config)
    return result


if __name__ == "__main__":
    print(ask("Who did Geelong play in Round 5 of 2020?", thread_id="t1")["final_response"])
    print(ask("Who had the most disposals for Geelong in that match?", thread_id="t1")["final_response"])
    print(ask("Who will win Cats vs Pies?", thread_id="t2")["final_response"])
    print(ask("What does holding the ball mean?", thread_id="t3")["final_response"])
    print(ask("What is the offside rule in soccer?", thread_id="t4")["final_response"])
    print(ask("Who will win?", thread_id="t5")["final_response"])
