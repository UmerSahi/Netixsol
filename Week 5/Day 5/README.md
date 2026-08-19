# Web3Geeks Support Triage Agent

Capstone: a LangGraph agent that triages support tickets (refunds,
technical bugs, general pricing questions) for a small freelance web3
dev agency, drafts a client-ready reply, and — for any refund — pauses
for a human to approve before it can resolve. Wrapped behind a FastAPI
service with structured logging.

See `executive_report.pdf` for the business case, architecture, and
evaluation summary, and `architecture_diagram.png` for the full graph.

## Project structure

| File | Purpose |
|---|---|
| `agent_core.py` | The agent itself — LangGraph `StateGraph`, all nodes, tools, and the human-approval interrupt. Single source of truth, imported by both `app.py` and the notebook. |
| `seed_db.py` | Builds `tickets.db` (SQLite orders table) that `billing_lookup` reads from. |
| `app.py` | FastAPI wrapper: `POST /tickets`, `POST /tickets/{id}/approve`, `GET /healthz`. Writes structured JSON logs to `agent_runs.log`. |
| `evaluate.py` | Evaluation harness — 9 test cases (7 normal + 2 adversarial) scored against 6 criteria, run live against the graph. |
| `make_diagram.py` | Regenerates `architecture_diagram.png` from the graph definition (uses `graphviz`). |
| `capstone_agent_system.ipynb` | Walks through Tasks 1–5: design rationale, build + failure-scenario demos, evaluation run, API smoke test, final deliverables. |
| `tickets.db` | Seeded SQLite database (output of `seed_db.py`). |
| `evaluation_results.csv` | Output of the most recent `evaluate.py` run. |
| `agent_runs.log` | Output of the most recent `app.py` run — one JSON line per request. |
| `executive_report.pdf` | 2-page business report: goal, architecture, framework rationale, evaluation results, limitations, next steps. |
| `monitoring_checklist.md` | One-page production monitoring checklist — what to track, alert thresholds, re-evaluation cadence. |
| `slide_outline.md` | 5–7 minute stakeholder presentation outline. |

## Setup

Requires Python 3.10+. Install dependencies:

```bash
pip install langgraph httpx fastapi uvicorn "pydantic[email]" pandas litellm graphviz
```

This agent always calls the live Gemini API — there is no offline
fallback by design. Set your key before running anything that invokes
`run_ticket()`:

```bash
export GEMINI_API_KEY=your-key-here
```

(Get a key at https://aistudio.google.com/apikey.) `app.py` and
`evaluate.py` will both fail fast with a clear message if this isn't
set.

Then seed the local database once:

```bash
python seed_db.py
```

## Running things

**API server:**
```bash
uvicorn app:app --reload --port 8000
```
```bash
curl -X POST localhost:8000/tickets -H "Content-Type: application/json" \
  -d '{"customer_email": "amelia@example.com", "text": "Refund ORD-1001 please"}'
# then, to resolve the refund it pauses on:
curl -X POST localhost:8000/tickets/<ticket_id>/approve -H "Content-Type: application/json" \
  -d '{"approved": true}'
```

**Evaluation harness** (calls the live model for every test case —
costs a small amount of real money and API quota):
```bash
python evaluate.py
```
Prints the results table, the most common failure pattern, and saves
`evaluation_results.csv`.

**Regenerate the architecture diagram:**
```bash
python make_diagram.py
```

**Notebook:** open `capstone_agent_system.ipynb` and run top to bottom
(set `GEMINI_API_KEY` in the second cell if running outside Colab).

## Known limitations

See `executive_report.pdf` for the full list. Headline items: the
evaluation harness has only been run against a subset of the 9 test
cases so far (Gemini API rate limit) — both adversarial cases still
need a run before the safety criterion is fully validated — and
`InMemorySaver` is a demo-appropriate checkpointer, not a
production-durable one.
