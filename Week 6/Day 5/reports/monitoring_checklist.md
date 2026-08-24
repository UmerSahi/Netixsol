# AFL Agent — Monitoring & Maintenance Checklist

_One-page operational reference. Pairs with the structured JSON-line logs
written by `api.py` to `logs/requests.jsonl` (fields: timestamp,
conversation_id, query, intent, tool_called, router_source,
validation_status, latency_ms, grounded, ok, error)._

## What to track

| Metric | Source | Why it matters |
|---|---|---|
| **Response latency (p50/p95)** | `latency_ms` per log line | Tool calls run through a hard timeout (`AFL_TOOL_TIMEOUT_SECONDS`, default 8s); a rising p95 signals a slow data load, a growing dataset, or a hung Gemini call before it starts timing out. |
| **Tool error rate** | `ok: false` + `validation_status: "failed"` share of requests | A spike means a tool is throwing (bad data, schema drift) rather than cleanly returning `{ok: False, error: ...}` — every tool call is wrapped in `_safe_call`, so a raw 500 from `/chat` is always a real bug, not expected behavior. |
| **Clarification/ambiguous rate** | `validation_status: "ambiguous"` share | A sustained rise suggests the router or entity extractor is under-resolving real questions — a leading indicator for router regressions before users complain. |
| **Off-topic / injection leak rate** | `intent: "off_topic"` responses that do NOT match a refusal phrase, sampled manually or by keyword-matching `logs/requests.jsonl` against `_REFUSAL_OFF_TOPIC` / `_REFUSAL_INJECTION` | The scope guard is marker-based; new injection phrasings won't match `_INJECTION_MARKERS` until added. This is the metric that catches guard drift in the wild. |
| **Router source split** | `router_source: "gemini"` vs `"rule_based"` | If `GOOGLE_API_KEY` is set but `rule_based` dominates, the Gemini path is silently failing (`GET /router-status` gives a one-shot check; set `AFL_ROUTER_DEBUG=1` for the real exception in server logs). |
| **Grounding failures** | `grounded: false` in logs | The response formatter self-corrects to raw tool data when a number can't be traced to the tool result — any non-zero rate here means a formatter template is producing figures the tool never returned, and needs a direct code fix, not just monitoring. |
| **Prediction accuracy drift** | Compare `predict_match_winner` predictions logged per round against real results once published (join on team/date) | The model was trained on data through 2025; accuracy silently decaying as new seasons diverge from training-era team strength is the single biggest long-run risk to prediction quality. |
| **Token usage** (Gemini path only) | LangChain response metadata, log alongside `router_source: "gemini"` | Cost control if the LLM router is enabled at scale. |

## Alert thresholds (starting point — tune after 2–4 weeks of real traffic)

- p95 latency > 3s sustained for 15 min → investigate (tool timeout is 8s; this should almost never be hit under normal load).
- Tool error rate > 2% over any rolling 1-hour window → page on-call.
- Off-topic/injection leak rate > 0 confirmed leaks in manual weekly review → treat as a P1 bug fix, not just a monitoring note (add the phrasing to `_INJECTION_MARKERS` / `_OFF_TOPIC_MARKERS` immediately).
- Router source unexpectedly "rule_based" for >10% of requests when a key is configured → check `/router-status` and Gemini quota/billing.
- Match-winner accuracy on newly completed rounds falls more than 5 points below the ~66% test-set baseline over a rolling 4-round window → trigger retraining review (see below), don't wait for the scheduled cycle.

## Weekly retraining / refresh loop

1. **Data refresh (after each round completes):** append newly completed matches to `afl_match_retrieval.csv` / `afl_player_retrieval.csv`, then regenerate `afl_match_prediction_features.csv` / `afl_player_prediction_features.csv` using the same leakage-safe feature definitions in `prediction_tools.build_match_features_for_prediction` (the pipeline is verified to reproduce the training file exactly — see README) so the new rows are consistent with everything the models were trained on.
2. **Re-evaluate before retraining:** run `python router_eval.py`, `python eval_suite.py`, and compare the new round's actual results against what `predict_match_winner` predicted going in (accuracy drift check above). Retraining on a model that isn't actually drifting just adds noise.
3. **Retrain on a fixed weekly cadence (not every round):** run `python train.py` once per week (or immediately if the drift alert above fires), which chronologically re-splits train/val/test using the now-larger dataset — the split logic (`_determine_split`) automatically shifts train/val/test forward as new seasons accumulate, so no manual date-range editing is needed.
4. **Gate the swap:** only replace the live `artifacts/*.joblib` files if the new model's validation AUC/MAE is at least as good as the current one on the same validation season; otherwise investigate before deploying (a naive baseline comparison, as in `eval_suite.naive_baseline_comparison`, should also be re-run so a regression is caught relative to the floor, not just relative to the old model).
5. **Re-run `generate_reports.py` and `build_executive_report.py`** after every retrain so `reports/` always reflects the currently-deployed model, and archive the previous `artifacts/` + `reports/` directory by date for rollback.
