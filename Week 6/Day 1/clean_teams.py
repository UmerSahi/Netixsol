"""
Canonical team name mapping for the AFL dataset.

Across the 4 raw tables, team names appear with inconsistent whitespace,
casing, ALL-CAPS blocks, tab characters, and abbreviations (e.g. "W. Bulldogs").
This module normalises every variant to one canonical name per club, and
also encodes historical club identity changes (rename / relocation / merger)
so analysts can choose whether to treat those as the same franchise or not.
"""
import re
import pandas as pd

# canonical (current) club name -> list of raw-string variants seen in the data
CANONICAL_MAP = {
    "Adelaide Crows": ["adelaide crows", "ADELAIDE CROWS", "Adelaide Crows"],
    "Brisbane Bears": ["brisbane bears", "BRISBANE BEARS", "Brisbane Bears"],
    "Brisbane Lions": ["brisbane lions", "BRISBANE LIONS", "Brisbane Lions"],
    "Carlton Blues": ["carlton blues", "CARLTON BLUES", "Carlton Blues"],
    "Collingwood Magpies": ["collingwood magpies", "COLLINGWOOD MAGPIES", "Collingwood Magpies"],
    "Essendon Bombers": ["essendon bombers", "ESSENDON BOMBERS", "Essendon Bombers"],
    "Fitzroy Lions": ["fitzroy lions", "FITZROY LIONS", "Fitzroy Lions"],
    "Fremantle Dockers": ["fremantle dockers", "FREMANTLE DOCKERS", "Fremantle Dockers"],
    "Geelong Cats": ["geelong cats", "GEELONG CATS", "Geelong Cats"],
    "Gold Coast Suns": ["gold coast suns", "GOLD COAST SUNS", "Gold Coast Suns"],
    "Greater Western Sydney Giants": [
        "greater western sydney giants", "GREATER WESTERN SYDNEY GIANTS",
        "Greater Western Sydney Giants",
    ],
    "Hawthorn Hawks": ["hawthorn hawks", "HAWTHORN HAWKS", "Hawthorn Hawks"],
    "Melbourne Demons": ["melbourne demons", "MELBOURNE DEMONS", "Melbourne Demons"],
    "North Melbourne Kangaroos": [
        "north melbourne kangaroos", "NORTH MELBOURNE KANGAROOS",
        "North Melbourne Kangaroos",
    ],
    "Port Adelaide Power": ["port adelaide power", "PORT ADELAIDE POWER", "Port Adelaide Power"],
    "Richmond Tigers": ["richmond tigers", "RICHMOND TIGERS", "Richmond Tigers"],
    "St Kilda Saints": ["st kilda saints", "ST KILDA SAINTS", "St Kilda Saints"],
    "Sydney Swans": ["sydney swans", "SYDNEY SWANS", "Sydney Swans"],
    "West Coast Eagles": ["west coast eagles", "WEST COAST EAGLES", "West Coast Eagles"],
    "Western Bulldogs": ["western bulldogs", "WESTERN BULLDOGS", "Western Bulldogs", "W. Bulldogs"],
}

# Flatten into a fast lookup keyed on a normalised (stripped, lowercased,
# whitespace-collapsed) version of every variant string.
def _norm_key(s: str) -> str:
    s = str(s).replace("\t", " ").replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s

_LOOKUP = {}
for canon, variants in CANONICAL_MAP.items():
    _LOOKUP[_norm_key(canon)] = canon
    for v in variants:
        _LOOKUP[_norm_key(v)] = canon


def canonical_team(raw: str) -> str:
    """Map any raw team-name string variant to its canonical club name.
    Returns pd.NA if the value can't be resolved (so bad rows surface, not
    silently vanish)."""
    if pd.isna(raw):
        return pd.NA
    key = _norm_key(raw)
    return _LOOKUP.get(key, pd.NA)


def canonical_series(s: pd.Series) -> pd.Series:
    return s.map(canonical_team)


# --- Club identity / continuity notes (documented, not auto-merged) -------
# These are real-world franchise events relevant to any model that uses
# team history/rolling form. We keep raw club names as reported (they are
# already how the source data identifies teams across their history) and
# expose this table so analysts can decide whether to bridge continuity.
CLUB_HISTORY_NOTES = [
    ("Fitzroy Lions", "Fitzroy Lions merged with Brisbane Bears to form Brisbane Lions "
                       "ahead of the 1997 season. Treat Fitzroy Lions and Brisbane Bears "
                       "as distinct historical entities from Brisbane Lions; do not bridge "
                       "rolling-form features across the merger."),
    ("Brisbane Bears", "Brisbane Bears (1987-1996) merged into Brisbane Lions for 1997. "
                        "See Fitzroy Lions note."),
    ("Brisbane Lions", "Entered competition 1997 as the Fitzroy/Brisbane Bears merger club."),
    ("Fremantle Dockers", "Expansion club, entered 1995."),
    ("Port Adelaide Power", "Expansion club, entered 1997."),
    ("Gold Coast Suns", "Expansion club, entered 2011."),
    ("Greater Western Sydney Giants", "Expansion club, entered 2012."),
    ("Western Bulldogs", "Known as Footscray Bulldogs before a 1997 rebrand; this dataset "
                          "already reports the club uniformly as 'Western Bulldogs' / "
                          "'W. Bulldogs' across its history, both mapped to one canonical name."),
]

if __name__ == "__main__":
    print(f"{len(CANONICAL_MAP)} canonical teams, {len(_LOOKUP)} raw variants mapped.")
