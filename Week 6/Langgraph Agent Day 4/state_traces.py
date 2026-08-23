"""
state_traces.py
=================
Section 22: State trace logging for 3 representative conversations
(retrieval, prediction, ambiguous/failed), using the `trace` list that
every node appends to in the real compiled graph -- not hand-written.
"""
from __future__ import annotations
from graph import ask


def print_trace(title: str, query: str, thread_id: str):
    print("=" * 70)
    print(title)
    print("=" * 70)
    print(f"USER QUERY: {query}")
    result = ask(query, thread_id=thread_id)
    print("    |")
    for line in result["trace"]:
        print(f"    v\n  {line}")
    print("    |\n    v")
    print(f"FINAL RESPONSE: {result['final_response']}")
    print()


if __name__ == "__main__":
    print_trace("TRACE 1 -- Retrieval example", "Who did Geelong play in Round 5 of 2020?", "trace1")
    print_trace("TRACE 2 -- Prediction example", "Who will win Cats vs Pies?", "trace2")
    print_trace("TRACE 3 -- Ambiguous/failed example", "How many disposals did Smith have in round 1 2022?", "trace3")
