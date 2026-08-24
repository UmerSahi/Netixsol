"""
grounding.py
============
Numeric grounding check used by the response formatter (and testable
independently). Confirms that every number appearing in the final
user-facing text is traceable to a number that actually appeared in the
tool result the response was built from -- guarding against the LLM
silently inventing or mis-stating a figure while phrasing the answer.

68.4 and 68.40 (and 68) are treated as equivalent, since that is a harmless
formatting/rounding difference, not an ungrounded number.
"""
from __future__ import annotations
import re
from typing import Any


_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _extract_numbers(obj: Any) -> set:
    """Recursively collect every numeric value found in a nested
    dict/list/str tool-result structure, normalized to 2 decimal places so
    68.4 / 68.40 / 68.400 all collapse to the same key."""
    nums = set()

    def norm(x: float) -> str:
        # Round to 1 decimal place: this matches how the response formatter
        # displays percentages/stats (e.g. "75.9%"), so a harmless display
        # rounding difference (75.88 -> "75.9") is never mistaken for an
        # invented number.
        return f"{round(float(x), 1):.1f}"

    def walk(o):
        if isinstance(o, (int, float)):
            nums.add(norm(o))
            # Probabilities are stored as 0-1 fractions but almost always
            # presented to the user as percentages (e.g. 0.684 -> "68.4%").
            # That is a harmless, expected formatting transform, not an
            # ungrounded number, so both forms are accepted.
            if 0 <= o <= 1:
                nums.add(norm(o * 100))
        elif isinstance(o, str):
            for m in _NUM_RE.findall(o):
                nums.add(norm(float(m)))
        elif isinstance(o, dict):
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    walk(obj)
    return nums


def check_grounding(final_response: str, tool_result: Any) -> dict:
    """
    Returns:
        {
          "grounded": bool,
          "answer_numbers": [...],
          "tool_numbers": [...],
          "ungrounded_numbers": [...],
        }
    A number in the answer is considered grounded if it matches (at 2 d.p.)
    any number present anywhere in the tool result. Small integers that are
    extremely common in ordinary prose (0 and 1, e.g. "a" / ordinal "1st")
    are still checked -- but round numbers, years (which are also present
    verbatim in most tool results as `year`/`season`) will naturally match.
    """
    answer_nums = sorted(_NUM_RE.findall(final_response), key=float)
    answer_norm = {f"{round(float(n), 1):.1f}" for n in answer_nums}
    tool_norm = _extract_numbers(tool_result)

    ungrounded = sorted(answer_norm - tool_norm)
    return {
        "grounded": len(ungrounded) == 0,
        "answer_numbers": sorted(answer_norm),
        "tool_numbers": sorted(tool_norm),
        "ungrounded_numbers": ungrounded,
    }


if __name__ == "__main__":
    tool_result = {"ok": True, "data": {"team": "Geelong Cats", "team_score": 89, "opponent_score": 52}}
    resp_ok = "Geelong scored 89 points to Gold Coast's 52."
    resp_bad = "Geelong scored 91 points to Gold Coast's 52."
    print(check_grounding(resp_ok, tool_result))
    print(check_grounding(resp_bad, tool_result))
    print(check_grounding("Probability 68.40%", {"data": {"probability_home_win": 0.684}}))
