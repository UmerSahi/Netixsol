"""
generate_reports.py
====================
Runs the real router evaluation, end-to-end conversations, state traces,
and (if trained) model metrics, and writes each as a polished Markdown
file plus one combined submission report. Nothing here is hand-written --
every number and every conversation turn comes from actually executing the
real graph/router/models at run time.

Usage:
    python generate_reports.py

Writes into ./reports/ (created next to this file):
    router_evaluation.md
    end_to_end_conversations.md
    state_traces.md
    model_training_report.md   (only if models are already trained)
    submission_report.md       (all of the above combined)
"""
from __future__ import annotations
import os
import json
import datetime

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(THIS_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


def _write(fname: str, content: str):
    path = os.path.join(REPORTS_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Wrote {path}")
    return content


# ---------------------------------------------------------------------------
# Router evaluation
# ---------------------------------------------------------------------------
def build_router_evaluation_md() -> str:
    from router_eval import run_eval

    df = run_eval()
    acc = df["correct"].mean()
    n_correct = int(df["correct"].sum())
    n_total = len(df)
    misroutes = df[~df["correct"]]

    lines = []
    lines.append("# Router Evaluation\n")
    lines.append(f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")
    lines.append(f"**Routing accuracy: {acc:.2%} ({n_correct}/{n_total})**\n")
    lines.append("| Query | Expected Intent | Predicted Intent | Correct |")
    lines.append("|---|---|---|---|")
    for _, row in df.iterrows():
        mark = "✅" if row["correct"] else "❌"
        q = row["query"].replace("|", "\\|")
        lines.append(f"| {q} | `{row['expected_intent']}` | `{row['predicted_intent']}` | {mark} |")
    lines.append("")
    if len(misroutes):
        lines.append("## Misroutes\n")
        for _, row in misroutes.iterrows():
            lines.append(f"- **{row['query']}** — expected `{row['expected_intent']}`, "
                         f"got `{row['predicted_intent']}`. {row['explanation']}")
    else:
        lines.append("## Misroutes\n\nNone — all queries routed correctly.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# End-to-end conversations
# ---------------------------------------------------------------------------
def build_e2e_conversations_md() -> str:
    from e2e_conversations import run_all

    results = run_all()  # also prints to stdout as before

    lines = []
    lines.append("# End-to-End Conversations\n")
    lines.append(f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")
    lines.append(f"{len(results)} conversations, run through the real compiled LangGraph "
                 f"(no hand-written responses).\n")
    for convo in results:
        lines.append(f"## {convo['name']}")
        lines.append(f"_Covers: {', '.join(convo['covers'])}_\n")
        for turn in convo["turns"]:
            lines.append(f"**User:** {turn['user']}")
            lines.append(f"**Assistant:** {turn['bot']}")
            lines.append(f"> intent: `{turn['intent']}` · validation: `{turn['validation']}`\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# State traces
# ---------------------------------------------------------------------------
def build_state_traces_md() -> str:
    from graph import ask

    lines = []
    lines.append("# Annotated State Traces\n")
    lines.append(f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")

    trace_cases = [
        ("Trace 1 — Retrieval example", "Who did Geelong play in Round 5 of 2020?", "report_trace1"),
        ("Trace 2 — Prediction example", "Who will win Cats vs Pies?", "report_trace2"),
        ("Trace 3 — Ambiguous/failed example", "How many disposals did Smith have in round 1 2022?", "report_trace3"),
        ("Trace 4 — Season/premiership prediction example", "Who will win AFL in 2030?", "report_trace4"),
    ]
    for title, query, thread_id in trace_cases:
        result = ask(query, thread_id=thread_id)
        lines.append(f"## {title}\n")
        lines.append(f"**User query:** {query}\n")
        lines.append("```")
        for step in result["trace"]:
            lines.append(step)
        lines.append("```\n")
        lines.append(f"**Final response:** {result['final_response']}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Model training report (only if models already trained)
# ---------------------------------------------------------------------------
def build_model_training_report_md() -> str | None:
    from model_training import MODEL_DIR

    meta_files = {
        "Match winner (classification)": "match_winner_metadata.json",
        "Player top-disposals (classification)": "top_disposals_metadata.json",
        "Player expected disposals (regression)": "expected_disposals_metadata.json",
    }
    rows = []
    for label, fname in meta_files.items():
        path = os.path.join(MODEL_DIR, fname)
        if not os.path.exists(path):
            return None  # models not trained yet -- skip this section entirely
        with open(path) as f:
            meta = json.load(f)
        rows.append((label, meta))

    lines = []
    lines.append("# Model Training Report\n")
    lines.append(f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n")
    lines.append("All models are split strictly chronologically by season (no shuffling).\n")
    lines.append("| Task | Model | Train seasons | Val season | Test seasons | Val metric | Test metric |")
    lines.append("|---|---|---|---|---|---|---|")
    for label, meta in rows:
        lines.append(
            f"| {label} | `{meta['model_type']}` | {meta['train_seasons']} | {meta['val_season']} | "
            f"{meta['test_seasons']} | {meta['val_metric_name']}={meta['val_metric_value']:.4f} | "
            f"{meta['test_metric_name']}={meta['test_metric_value']:.4f} |"
        )
    lines.append("")
    for label, meta in rows:
        lines.append(f"### {label}\n")
        lines.append(f"- **Target column:** `{meta['target']}`")
        lines.append(f"- **Feature count:** {len(meta['feature_columns'])}")
        lines.append(f"- **Notes:** {meta['notes']}\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    router_md = _write("router_evaluation.md", build_router_evaluation_md())
    e2e_md = _write("end_to_end_conversations.md", build_e2e_conversations_md())
    traces_md = _write("state_traces.md", build_state_traces_md())

    model_md = build_model_training_report_md()
    if model_md:
        model_md = _write("model_training_report.md", model_md)
    else:
        print("Skipping model_training_report.md -- models not trained yet "
              "(run `python model_training.py` first if you want this section).")

    combined = ["# AFL LangGraph Agent — Submission Report\n",
                f"_Generated {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}_\n",
                "---\n"]
    if model_md:
        combined.append(model_md)
        combined.append("\n---\n")
    combined.append(router_md)
    combined.append("\n---\n")
    combined.append(e2e_md)
    combined.append("\n---\n")
    combined.append(traces_md)
    _write("submission_report.md", "\n".join(combined))

    print(f"\nAll reports written to: {REPORTS_DIR}")


if __name__ == "__main__":
    main()
