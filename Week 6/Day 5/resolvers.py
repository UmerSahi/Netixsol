"""
resolvers.py
============
Section 6: Team and player resolution.

Both resolvers follow a strict "never guess" resolution order and return a
structured ResolutionResult so downstream nodes can distinguish between
FOUND / AMBIGUOUS / NOT_FOUND without ever silently picking a candidate.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
import difflib
import pandas as pd

from data_layer import get_dataset, get_known_teams

# ---------------------------------------------------------------------------
# Documented, hand-audited nickname/alias map. Every alias here is checked
# against the ACTUAL team names returned by get_known_teams() at import time
# (see _validate_alias_map) -- if a target team disappears from the data this
# will raise loudly instead of silently resolving to a stale/nonexistent team.
# ---------------------------------------------------------------------------
_ALIAS_MAP = {
    "cats": "Geelong Cats",
    "geelong": "Geelong Cats",
    "pies": "Collingwood Magpies",
    "magpies": "Collingwood Magpies",
    "collingwood": "Collingwood Magpies",
    "swans": "Sydney Swans",
    "sydney": "Sydney Swans",
    "giants": "Greater Western Sydney Giants",
    "gws": "Greater Western Sydney Giants",
    "greater western sydney": "Greater Western Sydney Giants",
    "dockers": "Fremantle Dockers",
    "fremantle": "Fremantle Dockers",
    "freo": "Fremantle Dockers",
    "eagles": "West Coast Eagles",
    "west coast": "West Coast Eagles",
    "blues": "Carlton Blues",
    "carlton": "Carlton Blues",
    "bombers": "Essendon Bombers",
    "essendon": "Essendon Bombers",
    "dons": "Essendon Bombers",
    "tigers": "Richmond Tigers",
    "richmond": "Richmond Tigers",
    "hawks": "Hawthorn Hawks",
    "hawthorn": "Hawthorn Hawks",
    "demons": "Melbourne Demons",
    "melbourne": "Melbourne Demons",
    "dees": "Melbourne Demons",
    "kangaroos": "North Melbourne Kangaroos",
    "north melbourne": "North Melbourne Kangaroos",
    "roos": "North Melbourne Kangaroos",
    "saints": "St Kilda Saints",
    "st kilda": "St Kilda Saints",
    "power": "Port Adelaide Power",
    "port adelaide": "Port Adelaide Power",
    "port": "Port Adelaide Power",
    "crows": "Adelaide Crows",
    "adelaide": "Adelaide Crows",
    "suns": "Gold Coast Suns",
    "gold coast": "Gold Coast Suns",
    "bulldogs": "Western Bulldogs",
    "dogs": "Western Bulldogs",
    "western bulldogs": "Western Bulldogs",
    "lions": "Brisbane Lions",  # Brisbane Bears existed pre-1997 merger; both
    "brisbane": "Brisbane Lions",  # are real distinct historical teams in the
    "bears": "Brisbane Bears",     # data, so 'bears' must map separately.
}


def _validate_alias_map():
    known = set(get_known_teams())
    bad = {alias: target for alias, target in _ALIAS_MAP.items() if target not in known}
    if bad:
        raise ValueError(f"Alias map references teams not present in the data: {bad}")


_validate_alias_map()


@dataclass
class ResolutionResult:
    status: str  # "found" | "ambiguous" | "not_found"
    value: Optional[str] = None          # resolved canonical value (team/player)
    candidates: List[str] = field(default_factory=list)  # for ambiguous/not_found suggestions
    message: str = ""


def resolve_team(raw: str) -> ResolutionResult:
    """
    Resolve a free-text team reference to exactly one canonical team name
    using the documented resolution order. Never silently picks between
    multiple plausible teams.
    """
    if not raw or not raw.strip():
        return ResolutionResult("not_found", message="No team name was provided.")
    q = raw.strip()
    ql = q.lower()
    known = get_known_teams()
    known_lower = {t.lower(): t for t in known}

    # 1. Exact canonical match
    if q in known:
        return ResolutionResult("found", value=q)

    # 2. Case-insensitive match
    if ql in known_lower:
        return ResolutionResult("found", value=known_lower[ql])

    # 3. Exact validated alias
    if ql in _ALIAS_MAP:
        return ResolutionResult("found", value=_ALIAS_MAP[ql])

    # 4. Unique partial match against canonical names
    partial = [t for t in known if ql in t.lower()]
    if len(partial) == 1:
        return ResolutionResult("found", value=partial[0])
    if len(partial) > 1:
        return ResolutionResult(
            "ambiguous", candidates=partial,
            message=f"'{raw}' matches multiple teams: {', '.join(partial)}. Which one did you mean?"
        )

    # 5. Unique nickname/alias partial match
    alias_hits = {target for alias, target in _ALIAS_MAP.items() if ql in alias or alias in ql}
    if len(alias_hits) == 1:
        return ResolutionResult("found", value=next(iter(alias_hits)))
    if len(alias_hits) > 1:
        return ResolutionResult(
            "ambiguous", candidates=sorted(alias_hits),
            message=f"'{raw}' could refer to multiple teams: {', '.join(sorted(alias_hits))}. Which one did you mean?"
        )

    # 6/7. Nothing matched -- report not found (with fuzzy suggestions, never auto-picked)
    close = difflib.get_close_matches(q, known, n=3, cutoff=0.5)
    return ResolutionResult(
        "not_found", candidates=close,
        message=(f"I couldn't find a team matching '{raw}'." +
                  (f" Did you mean: {', '.join(close)}?" if close else ""))
    )


def resolve_player(raw: str) -> ResolutionResult:
    """
    Resolve a free-text player reference to exactly one canonical
    player_name from afl_player_retrieval.csv using the documented order.
    """
    if not raw or not raw.strip():
        return ResolutionResult("not_found", message="No player name was provided.")
    q = raw.strip()
    ql = q.lower()

    pr = get_dataset("player_retrieval")
    names = pr["player_name"].dropna().unique()
    names_lower = {n.lower(): n for n in names}

    # 1. Exact case-insensitive full-name match
    if ql in names_lower:
        return ResolutionResult("found", value=names_lower[ql])

    # 2. Normalized match (strip extra whitespace / punctuation-insensitive)
    def norm(s):
        return " ".join(s.lower().replace(".", "").replace("-", " ").split())
    q_norm = norm(q)
    norm_map = {}
    for n in names:
        norm_map.setdefault(norm(n), []).append(n)
    if q_norm in norm_map and len(norm_map[q_norm]) == 1:
        return ResolutionResult("found", value=norm_map[q_norm][0])
    if q_norm in norm_map and len(norm_map[q_norm]) > 1:
        return ResolutionResult(
            "ambiguous", candidates=norm_map[q_norm],
            message=f"I found multiple players matching '{raw}': {', '.join(norm_map[q_norm])}. Which player do you mean?"
        )

    # 3. Unique partial match only if unambiguous (e.g. last-name-only queries)
    partial = [n for n in names if ql in n.lower()]
    if len(partial) == 1:
        return ResolutionResult("found", value=partial[0])
    if len(partial) > 1:
        # cap suggestion list for readability
        shown = sorted(partial)[:10]
        more = "" if len(partial) <= 10 else f" (+{len(partial) - 10} more)"
        return ResolutionResult(
            "ambiguous", candidates=partial,
            message=f"I found multiple players matching '{raw}': {', '.join(shown)}{more}. Which player do you mean?"
        )

    close = difflib.get_close_matches(q, list(names), n=3, cutoff=0.6)
    return ResolutionResult(
        "not_found", candidates=close,
        message=(f"I couldn't find a player named '{raw}' in the dataset." +
                  (f" Did you mean: {', '.join(close)}?" if close else ""))
    )


if __name__ == "__main__":
    for t in ["Cats", "pies", "GWS", "west coast", "geelong cats", "Lions", "Bears", "nope"]:
        print(t, "->", resolve_team(t))
    for p in ["patrick dangerfield", "dangerfield", "tony lockett", "paul salmon", "nobody here"]:
        print(p, "->", resolve_player(p))
