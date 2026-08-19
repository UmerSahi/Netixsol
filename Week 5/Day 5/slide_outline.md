# Slide Outline — Web3Geeks Support Triage Agent
### 5–7 minute stakeholder walkthrough

**1. The problem (30s)**
- Shared support inbox gets three kinds of tickets: billing/refunds, technical bugs, general pricing — plus occasional social-engineering attempts aimed at getting a refund approved without review.
- Goal: triage automatically, draft client-ready replies, but never let a refund go out without a human signing off.

**2. What we built (45s)**
- A LangGraph agent: validates input → classifies the ticket → (for refunds) looks up the real order and converts currency → drafts a reply → self-critiques and retries → pauses for human approval → finalizes.
- Two real external dependencies: a SQLite orders table and the Frankfurter FX API.

**3. Architecture diagram (60s)**
- Walk the diagram: point out the three error-path nodes (reject_input, reject_injection, escalate_note) and the human-approval interrupt as the one node that isn't fully automated by design.

**4. Why LangGraph, not CrewAI (45s)**
- Fixed, control-heavy pipeline with real conditional branching and a bounded retry loop — the shape a `StateGraph` is built for.
- No genuine delegation ambiguity between specialist roles here; a crew adds coordination overhead for a task that has one clear path per category.

**5. Human-in-the-loop, live (60s — demo if possible)**
- Show a refund pausing at `human_approval_gate`, checkpointed via `InMemorySaver`, and only resolving after `Command(resume=True)`.
- This is the one guardrail that matters most: no refund reaches "resolved" without it.

**6. Evaluation results (60s)**
- 6 criteria: task success, grounding, latency, cost, tone, safety.
- Harness supports 9 test cases (7 normal + 2 adversarial: a direct prompt-injection attempt and a contradictory "override" instruction on an already-refunded order).
- **Status at this review:** 5 of 9 cases have run end-to-end and all scored cleanly (5/5 on everything except latency). The run stopped there because the harness hit the Gemini API rate limit — the remaining 4 cases, including **both adversarial ones**, haven't run yet. That's the top open item, not a nice-to-have: the completed cases prove the happy path works, not that the safety guardrails hold up under attack.

**7. Known limitations (45s)**
- Classifier can miss paraphrased refund requests that don't use obvious trigger words.
- `InMemorySaver` doesn't survive a process restart — fine for a demo, not for production.
- FX conversion depends on a free third-party API with no SLA.
- (New) Logging currently double-writes events on repeated imports — cosmetic but needs a fix before log-based alerting is trusted.

**8. Recommended next steps (45s)**
- Run the full 9-case eval suite (including both adversarial cases) before any go-live decision.
- Swap to a persistent checkpointer (e.g., Postgres) for real deployment.
- Add confidence-based escalation so low-confidence classifications route to a human instead of auto-resolving.
- Fix the duplicate-handler logging bug so production alerting isn't double-counting.

**9. Questions**
