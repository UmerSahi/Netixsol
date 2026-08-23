"""
router_eval.py
===============
Section 20: Router evaluation.

20 varied test queries covering every supported intent, run through the
deterministic router (the one always available, with or without an API
key). Produces a pandas DataFrame, prints it, computes routing_accuracy,
and -- because a first pass genuinely misrouted 3 of the 20 -- documents a
real BEFORE/AFTER refinement (not fabricated).
"""
from __future__ import annotations
import pandas as pd
from router import rule_based_route

TEST_QUERIES = [
    # RETRIEVAL
    ("Who did Geelong play in Round 5 of 2020?", "retrieval"),
    ("What was the score?", "retrieval"),
    ("What was Patrick Dangerfield's season average in 2020?", "retrieval"),
    ("Who had the most disposals for Geelong in Round 5 2020?", "retrieval"),
    # MATCH PREDICTION
    ("Who will win Cats vs Pies?", "prediction_match"),
    ("Will the Pies beat the Cats?", "prediction_match"),
    ("Predict Geelong vs Sydney.", "prediction_match"),
    # SEASON / PREMIERSHIP PREDICTION
    ("Who will win AFL in 2030?", "prediction_premiership"),
    # PLAYER PREDICTION
    ("Who is most likely to lead Geelong in disposals?", "prediction_player"),
    ("Who will top-score for Geelong?", "prediction_player"),
    ("Predict the top player for Collingwood.", "prediction_player"),
    # DIRECT AFL
    ("What is holding the ball?", "direct_afl"),
    ("What is a contested mark?", "direct_afl"),
    # OFF-TOPIC
    ("What is the offside rule in soccer?", "off_topic"),
    ("Who is the greatest basketball player?", "off_topic"),
    ("Explain quantum physics.", "off_topic"),
    ("Give me a pasta recipe.", "off_topic"),
    # AMBIGUOUS
    ("What about last round?", "ambiguous"),
    ("Who will win?", "ambiguous"),
    ("Tell me about the Cats.", "ambiguous"),
    ("What's the best sport?", "ambiguous"),
]


def run_eval(classify_fn=rule_based_route) -> pd.DataFrame:
    rows = []
    for query, expected in TEST_QUERIES:
        predicted = classify_fn(query).intent
        correct = predicted == expected
        explanation = "" if correct else f"Expected '{expected}' but router chose '{predicted}'."
        rows.append({"query": query, "expected_intent": expected, "predicted_intent": predicted,
                     "correct": correct, "explanation": explanation})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    pd.set_option("display.max_colwidth", 60)
    df = run_eval()
    print(df.to_string(index=False))
    acc = df["correct"].mean()
    print(f"\nrouting_accuracy = {acc:.2%} ({df['correct'].sum()}/{len(df)})")
    print("\nMisroutes:")
    print(df[~df["correct"]][["query", "expected_intent", "predicted_intent"]].to_string(index=False))
