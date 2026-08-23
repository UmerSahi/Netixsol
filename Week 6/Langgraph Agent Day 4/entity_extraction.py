"""
entity_extraction.py
=====================
Small helpers that pull candidate team/player substrings out of free text
for the nodes to hand to the resolvers. Deliberately conservative: these
only produce CANDIDATE strings, never a resolved value -- resolution and
ambiguity handling always goes through resolvers.resolve_team /
resolve_player, which is the only place allowed to declare something
"found".
"""
from __future__ import annotations
import re
from data_layer import get_known_teams
from resolvers import resolve_team
from router import _extract_year, _extract_round, _extract_stat  # reuse single source of truth

_ALIAS_TOKENS = ["cats", "pies", "swans", "giants", "gws", "dockers", "freo", "eagles",
                  "blues", "bombers", "dons", "tigers", "hawks", "demons", "dees",
                  "kangaroos", "roos", "saints", "power", "crows", "suns", "bulldogs",
                  "dogs", "lions", "bears"]


def find_team_candidates(text: str) -> list:
    """Return every DISTINCT canonical team referenced in `text`, whether via
    full/partial name or a known nickname token. Each canonical team appears
    at most once, in order of first mention, even if matched by multiple
    surface forms (e.g. both 'Geelong' and 'Cats' in the same message)."""
    tl = text.lower()
    resolved = []  # canonical names, in first-mention order

    def add(name: str):
        if name not in resolved:
            resolved.append(name)

    for team in get_known_teams():
        words = team.lower().split()
        for w in [team.lower()] + [w for w in words if len(w) > 3]:
            if re.search(rf"\b{re.escape(w)}\b", tl):
                add(team)
                break
    for alias in _ALIAS_TOKENS:
        if re.search(rf"\b{re.escape(alias)}\b", tl):
            r = resolve_team(alias)
            if r.status == "found":
                add(r.value)
    return resolved


_NON_PLAYER_PHRASES = {"grand final", "round number", "afl season", "the season", "last round",
                        "this week", "next round", "season average", "season total"}


def find_player_candidate(text: str) -> str | None:
    """Very conservative: look for 'Firstname Lastname' capitalized pattern.
    Never guesses a single-word name (too ambiguous) unless nothing else
    is available -- callers should still run it through resolve_player,
    which itself refuses to guess between multiple matches. Team names and
    common two-word AFL phrases (e.g. "Grand Final") are explicitly
    excluded so they're never mistaken for a player."""
    known_teams_lower = {t.lower() for t in get_known_teams()}
    for m in re.finditer(r"\b([A-Z][a-z]+(?:['\-][A-Z][a-z]+)?\s+[A-Z][a-z]+(?:['\-][A-Z][a-z]+)?)\b", text):
        candidate = m.group(1)
        cl = candidate.lower()
        if cl in known_teams_lower or cl in _NON_PLAYER_PHRASES:
            continue
        if any(cl in t for t in known_teams_lower):
            continue
        return candidate

    # Fallback: a single capitalized surname used with an explicit
    # possessive/query pattern ("did Smith have", "for Smith in") -- still
    # only a CANDIDATE, resolved (and disambiguated if needed) downstream
    # by resolve_player, never assumed correct here.
    m2 = re.search(r"\bdid\s+([A-Z][a-z]+)\s+(?:have|get|score|kick)\b", text)
    if m2 and m2.group(1).lower() not in known_teams_lower:
        return m2.group(1)
    m3 = re.search(r"\bfor\s+([A-Z][a-z]+)\s+in\s+round\b", text)
    if m3 and m3.group(1).lower() not in known_teams_lower:
        return m3.group(1)
    return None
