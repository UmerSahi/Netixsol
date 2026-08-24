"""
entity_extraction.py
=====================
Small helpers that pull candidate team/player substrings out of free text
for the nodes to hand to the resolvers. Deliberately conservative: these
only produce CANDIDATE strings, never a resolved value -- resolution and
ambiguity handling always goes through resolvers.resolve_team /
resolve_player, which is the only place allowed to declare something
"found".

CAPSTONE ADDITIONS (v2):
    - find_player_candidates(): returns up to 2 distinct capitalized-name
      candidates in first-mention order, so a two-player comparison
      ("Sam Walsh vs Lachie Neale disposals") can be detected. The
      original single-candidate find_player_candidate() is kept as a thin
      wrapper for backward compatibility with any code that still expects
      one name.
    - find_years(): returns EVERY distinct year mentioned (not just the
      first), so "combined 2022 and 2023" resolves to [2022, 2023] instead
      of silently keeping only one.
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

    known_teams = get_known_teams()
    for team in known_teams:
        if re.search(rf"\b{re.escape(team.lower())}\b", tl):
            add(team)
    for alias in _ALIAS_TOKENS:
        if re.search(rf"\b{re.escape(alias)}\b", tl):
            r = resolve_team(alias)
            if r.status == "found":
                add(r.value)
    word_owners = {}
    for team in known_teams:
        for word in set(team.lower().split()):
            if len(word) > 3:
                word_owners.setdefault(word, set()).add(team)
    for word, owners in word_owners.items():
        if len(owners) == 1 and re.search(rf"\b{re.escape(word)}\b", tl):
            add(next(iter(owners)))
        elif len(owners) > 1 and not any(owner in resolved for owner in owners):
            if re.search(rf"\b{re.escape(word)}\b", tl):
                add(min(owners, key=lambda owner: (len(owner.split()), len(owner))))
    # Preserve the order in which clubs appear in the question. This is
    # important for three-team comparisons where the first club is the
    # reference team and later clubs are its opponents.
    positions = {}
    for team in resolved:
        positions[team] = min(
            (tl.find(token) for token in [team.lower()] + team.lower().split() if tl.find(token) >= 0),
            default=len(text),
        )
    return sorted(resolved, key=lambda team: positions[team])


_NON_PLAYER_PHRASES = {"grand final", "round number", "afl season", "the season", "last round",
                        "this week", "next round", "season average", "season total",
                        "ignore previous", "ignore all", "ignore your", "system prompt",
                        "act as", "you are now"}

# Common sentence-initial question words that get capitalized purely by
# virtue of starting the sentence ("Did Collingwood win...", "Was Geelong
# ahead...") -- if the FIRST word of a two-word candidate is one of these,
# it is almost never actually someone's first name, so it's excluded to
# avoid misreading "Did Collingwood" as a player called "Did Collingwood".
_SENTENCE_STARTER_WORDS = {
    "did", "was", "is", "does", "will", "are", "has", "have", "should",
    "can", "could", "would", "who", "what", "how", "when", "where", "why",
    "predict", "show", "tell", "give", "compare", "find", "list",
}


def _known_team_name_words() -> set:
    """Every significant (len>3) individual word appearing in any known
    canonical team name, lowercased -- e.g. {'geelong', 'cats', ...} from
    'Geelong Cats'. Used to keep a team name's own words (not just its full
    name or a curated nickname) from being misread as half of a player
    name (e.g. 'Predict Geelong')."""
    words = set()
    for team in get_known_teams():
        for w in team.lower().split():
            if len(w) > 3:
                words.add(w)
    return words


def find_player_candidates(text: str, max_candidates: int = 2) -> list:
    """Return up to `max_candidates` DISTINCT 'Firstname Lastname'-style
    candidates found in `text`, in first-mention order. Conservative by
    design (see module docstring) -- never guesses a single-word name
    unless nothing else is available, and team names / common two-word AFL
    phrases are excluded so they are never mistaken for a player.

    This is the primary extractor used for player-vs-player comparison
    queries ("Sam Walsh vs Lachie Neale disposals"), where a single-result
    extractor would silently drop the second name.
    """
    known_teams_lower = {t.lower() for t in get_known_teams()}
    team_words = _known_team_name_words()
    found = []

    def add(name: str):
        if name not in found and len(found) < max_candidates:
            found.append(name)

    def _is_excluded(w1: str, w2: str) -> bool:
        cl = f"{w1.lower()} {w2.lower()}"
        words = [w1.lower(), w2.lower()]
        if cl in known_teams_lower or cl in _NON_PLAYER_PHRASES:
            return True
        if any(cl in t for t in known_teams_lower):
            return True
        if words[0] in _SENTENCE_STARTER_WORDS:
            return True
        # Neither word of the candidate should itself be a known team name,
        # a significant team-name word, or a nickname -- a genuine
        # two-word player name doesn't collide with an actual club
        # reference (e.g. avoids "Did Collingwood" or "Predict Geelong"
        # being read as a player).
        if any(w in known_teams_lower or w in team_words or w in _ALIAS_TOKENS for w in words):
            return True
        return False

    # Group consecutive capitalized tokens (separated only by whitespace)
    # into runs, then slide a 2-word window over EACH run rather than
    # relying on non-overlapping regex matching. This matters because a
    # sentence-initial verb ("Compare Marcus Bontempelli...") would
    # otherwise be consumed together with the real first name into one
    # rejected pair ("Compare Marcus"), silently skipping past the actual
    # player name ("Marcus Bontempelli") entirely instead of retrying
    # one word later within the same run.
    tokens = [(m.group(0), m.start(), m.end())
              for m in re.finditer(r"[A-Z][a-z]+(?:['\-][A-Z][a-z]+)?", text)]
    runs = []
    run = []
    for tok in tokens:
        if run and text[run[-1][2]:tok[1]].strip() == "":
            run.append(tok)
        else:
            if len(run) >= 2:
                runs.append(run)
            run = [tok]
    if len(run) >= 2:
        runs.append(run)

    for run in runs:
        words_in_run = [t[0] for t in run]
        for i in range(len(words_in_run) - 1):
            w1, w2 = words_in_run[i], words_in_run[i + 1]
            if _is_excluded(w1, w2):
                continue
            add(f"{w1} {w2}")
            break  # first valid pair per run only
        if len(found) >= max_candidates:
            break

    if found:
        return found

    # Fallback: a single capitalized surname used with an explicit
    # possessive/query pattern ("did Smith have", "for Smith in") -- still
    # only a CANDIDATE, resolved (and disambiguated if needed) downstream
    # by resolve_player, never assumed correct here.
    m2 = re.search(r"\bdid\s+([A-Z][a-z]+)\s+(?:have|get|score|kick)\b", text)
    if m2 and m2.group(1).lower() not in known_teams_lower:
        add(m2.group(1))
    m3 = re.search(r"\bfor\s+([A-Z][a-z]+)\s+in\s+round\b", text)
    if m3 and m3.group(1).lower() not in known_teams_lower:
        add(m3.group(1))
    return found


def find_player_candidate(text: str) -> str | None:
    """Backward-compatible single-candidate wrapper around
    find_player_candidates(). Never guesses between multiple matches --
    callers that need to detect a SECOND player (comparisons) should call
    find_player_candidates() directly instead."""
    cands = find_player_candidates(text, max_candidates=1)
    return cands[0] if cands else None


def find_years(text: str) -> list:
    """Return every DISTINCT year mentioned in `text`, in ascending order.
    Used for multi-season combined-stat queries ("tackles across 2022 and
    2023 combined") where the single-year extractor in router.py would
    only ever capture the first (or last) one."""
    years = sorted({int(y) for y in re.findall(r"\b(19[5-9]\d|20\d\d|21\d\d)\b", text)})
    return years


if __name__ == "__main__":
    print(find_player_candidates("Sam Walsh vs Lachie Neale disposals"))
    print(find_years("His combined disposals across 2022 and 2023"))
