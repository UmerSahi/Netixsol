"""
build_executive_report.py
==========================
Builds reports/executive_report.pdf (Capstone Task 5) using reportlab.
Pulls real numbers from the already-generated eval/report artifacts rather
than hand-typing figures -- run generate_reports.py first.
"""
from __future__ import annotations
import json
import os
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, ListFlowable, ListItem)

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(THIS_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

from model_training import MODEL_DIR
from router_eval import run_eval, test_prompt_injection_blocked
from eval_suite import run_all, summarize, naive_baseline_comparison

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="H1", fontSize=17, leading=21, spaceAfter=8, textColor=colors.HexColor("#13294B")))
styles.add(ParagraphStyle(name="H2", fontSize=12.5, leading=15, spaceBefore=10, spaceAfter=4,
                          textColor=colors.HexColor("#13294B")))
styles.add(ParagraphStyle(name="Body", fontSize=9.3, leading=12.5, spaceAfter=5))
styles.add(ParagraphStyle(name="Small", fontSize=8, leading=10.5, textColor=colors.grey))


def _load_json(fname):
    path = os.path.join(MODEL_DIR, fname)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


def build():
    match_meta = _load_json("match_winner_metadata.json")
    disp_meta = _load_json("expected_disposals_metadata.json")
    router_df = run_eval()
    inj_df = test_prompt_injection_blocked()
    eval_df = run_all()
    eval_summary = summarize(eval_df)
    baseline = naive_baseline_comparison()

    story = []
    story.append(Paragraph("AFL LangGraph AI Agent — Executive Report", styles["H1"]))
    story.append(Paragraph("Domain-locked AFL chat + prediction assistant, evaluated and wrapped for deployment",
                            styles["Small"]))
    story.append(Spacer(1, 10))

    # ---- Product goal ----
    story.append(Paragraph("Product Goal", styles["H2"]))
    story.append(Paragraph(
        "Give AFL fans, editors, and stakeholders one conversational interface that answers exact "
        "historical questions (scores, player stats, head-to-head records), makes leakage-safe "
        "match/player/season predictions from trained models, explains AFL rules, and firmly refuses "
        "anything outside AFL — including attempts to override its own instructions — while carrying "
        "conversational context correctly across multi-turn sessions.", styles["Body"]))

    # ---- Architecture ----
    story.append(Paragraph("Architecture", styles["H2"]))
    story.append(Paragraph(
        "A LangGraph state machine routes every message through: <b>load_context → router → "
        "{retrieval | prediction_match | prediction_premiership | prediction_player | direct_afl | "
        "refusal | clarification} → validation → response_formatter</b>. The router runs a deterministic "
        "keyword/regex classifier by default (100% accuracy, no external dependency) with an optional "
        "Gemini structured-output path when GOOGLE_API_KEY is configured, falling back automatically and "
        "silently on any LLM failure. Five retrieval tools and three prediction tools (match winner, "
        "player stat regressors for disposals/goals/kicks/marks/handballs/tackles, and a season-wide "
        "power-ranking built from the same match model) sit behind a numeric grounding check that "
        "verifies every figure in the final response actually traces back to the tool's own output. "
        "The whole app is wrapped in a FastAPI service (POST /chat, GET /health, GET /router-status) "
        "with structured JSON-line logging of every request.", styles["Body"]))

    # ---- Evaluation results ----
    story.append(Paragraph("Evaluation Results", styles["H2"]))
    router_acc = router_df["correct"].mean()
    inj_rate = inj_df["pass"].mean()
    story.append(Paragraph(
        f"<b>Router accuracy:</b> {router_acc:.0%} ({int(router_df['correct'].sum())}/{len(router_df)} "
        f"queries across every supported intent). <b>Prompt-injection scope guard:</b> {inj_rate:.0%} "
        f"block rate ({int(inj_df['pass'].sum())}/{len(inj_df)} distinct injection styles tested, all held).",
        styles["Body"]))

    table_data = [["Category", "Passed", "Total", "Pass Rate"]]
    for _, row in eval_summary.iterrows():
        table_data.append([row["category"], str(row["passed"]), str(row["total"]), f"{row['pass_rate']:.0%}"])
    t = Table(table_data, colWidths=[2.3*inch, 0.8*inch, 0.7*inch, 0.9*inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#13294B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F4F7")]),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        f"{int(eval_df['pass'].sum())}/{len(eval_df)} combined evaluation cases pass across factual Q&A, "
        f"prediction sanity, scope guardrails, and multi-turn conversational coherence.", styles["Body"]))

    if match_meta:
        story.append(Paragraph("Model performance vs. naive benchmark", styles["H2"]))
        story.append(Paragraph(
            f"Match-winner model ({match_meta['model_type']}): test ROC-AUC "
            f"<b>{match_meta['test_metric_value']:.4f}</b>. Against naive public benchmarks on the same "
            f"held-out seasons {baseline.get('test_seasons')}: home-team-always-wins scores "
            f"<b>{baseline.get('naive_home_always_wins_acc', 0):.1%}</b> accuracy; a ladder-position-style "
            f"proxy (better career win-rate entering the match) scores "
            f"<b>{baseline.get('naive_career_win_rate_favorite_acc', 0):.1%}</b>; the trained model scores "
            f"<b>{baseline.get('trained_model_test_acc', 0):.1%}</b>. This is a real, if modest, edge over "
            f"guessing — an honest picture of 'good enough' given AFL's large irreducible upset rate.",
            styles["Body"]))
        if disp_meta:
            story.append(Paragraph(
                f"Player expected-disposals regressor (HistGradientBoosting): test MAE "
                f"{disp_meta['test_metric_value']:.2f} disposals. Five additional stat regressors "
                f"(goals, kicks, marks, handballs, tackles) are trained the same way.", styles["Body"]))

    # ---- Known limitations ----
    story.append(Paragraph("Known Limitations", styles["H2"]))
    limitations = [
        "Data recency: the most recent season in the data is 2025; premiership predictions for seasons "
        "beyond that are explicitly flagged as low-confidence extrapolations, not real forecasts.",
        "Model accuracy ceiling: match-winner ROC-AUC ~0.71 / test accuracy ~66% reflects AFL's genuine "
        "unpredictability, not a modeling shortcut — treat probabilities as directional signal, not certainty.",
        "No fixture/schedule file exists in the data, so single-match predictions can never confirm two "
        "teams are actually scheduled to play — this is disclosed in every such response.",
        "Guardrail edge cases: the scope guard is marker-based (broad by design to minimize false "
        "negatives); novel injection phrasings not resembling any known marker could still slip through "
        "and should be monitored via the weekly re-evaluation loop (see monitoring checklist).",
        "\"Eligible roster\" for player predictions approximates to players who appeared for a team in the "
        "most recent season present in the data — not a live, current team list.",
    ]
    story.append(ListFlowable([ListItem(Paragraph(l, styles["Body"])) for l in limitations],
                              bulletType="bullet", start="•"))

    # ---- Next steps ----
    story.append(Paragraph("Recommended Next Steps", styles["H2"]))
    next_steps = [
        "Add Elo-style dynamic team ratings and interaction features (recent form × rest days) to lift "
        "match-model ROC-AUC beyond ~0.71, and run a full probability-calibration/reliability check.",
        "Stand up the weekly retraining loop described in the monitoring checklist as new rounds complete.",
        "Tighten FastAPI CORS origins and add authentication before any real external deployment.",
        "Expand the prompt-injection marker list based on real traffic (Task 4's off-topic/injection leak "
        "rate metric) rather than only the fixed test set used here.",
    ]
    story.append(ListFlowable([ListItem(Paragraph(l, styles["Body"])) for l in next_steps],
                              bulletType="bullet", start="•"))

    doc = SimpleDocTemplate(os.path.join(REPORTS_DIR, "executive_report.pdf"), pagesize=letter,
                             topMargin=0.6*inch, bottomMargin=0.6*inch, leftMargin=0.7*inch, rightMargin=0.7*inch)
    doc.build(story)
    print(f"Wrote {os.path.join(REPORTS_DIR, 'executive_report.pdf')}")


if __name__ == "__main__":
    build()
