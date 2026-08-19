# Monitoring Checklist — Web3Geeks Support Triage Agent

One-page reference for what to track once this agent is live, what should
page someone, and how often to re-run the evaluation suite.

## What to track

| Metric | Source | Why it matters |
|---|---|---|
| **Error rate** | `agent_runs.log` `error` field ≠ null, per hour | Tool failures, LLM API errors, or bugs slipping past `ToolError` handling |
| **Escalation rate** | share of `status == "escalated"` | Spikes mean the classifier or `billing_lookup` is hitting more edge cases than expected |
| **Injection/rejection rate** | share of `status in {rejected_input, rejected_injection}` | A sudden rise may mean a bad upstream integration (rejected_input) or a real attack pattern (rejected_injection) |
| **Human-approval turnaround** | time between `ticket_submitted` (paused) and `ticket_approved` | Refunds sit un-actioned if this grows — a queue-depth problem, not a model problem |
| **Latency (p50/p95)** | sum of `trace[].latency_ms` per ticket | Watch the FX API call and `critique` retry chains specifically — those are the two variable-cost paths |
| **Cost drift** | `input_tokens` + `output_tokens` per ticket, converted at current Gemini rate | Token usage grows quietly if prompts or retry rates creep up; check the per-1K rate hasn't changed too |
| **Revision/retry rate** | `revision_count` at `MAX_REVISIONS` (2) | Tickets maxing out retries are effectively silent failures dressed as an escalation |
| **Output quality drift** | periodic manual sample of `final_response` vs. `tone`/`grounding` rubric from `evaluate.py` | Automated scores only catch what the rubric anticipates; sample manually too |
| **Safety** | any `status == resolved` with `human_approved` not `True` on a refund category | Should be structurally impossible given the graph wiring — treat any occurrence as a P0 routing bug, not a prompt issue |

## Suggested alert thresholds

- **Error rate** > 5% of tickets in a rolling hour → page on-call
- **Escalation rate** > 20% in a rolling day → notify support lead (likely a classifier or data issue, not urgent but needs attention)
- **Human-approval turnaround** > 4 hours for any pending refund → notify support lead directly
- **p95 latency** > 5s → investigate FX API / retry chain before it becomes a support complaint
- **Cost per ticket** > 2x the 7-day rolling average → check for a runaway retry loop or a prompt regression
- **Any safety-criterion failure** (unauthorized refund resolution) → immediate stop-ship, page engineering, do not wait for the next batch review

## Re-evaluation cadence

- **Weekly:** re-run `evaluate.py`'s full test suite (all 9 cases, not a subset) against the live model; diff scores against the previous week's baseline.
- **On every prompt or model-version change:** full eval suite before deploying, not after.
- **Monthly:** manual review of a random sample (~20) of real production tickets against the same 6 criteria, since the fixed test cases don't cover everything real customers will send.
- **Quarterly:** revisit the FX API dependency and checkpointer choice (see known limitations in the executive report) — both are demo-appropriate but not production-hardened.
