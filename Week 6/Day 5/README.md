# AFL LangGraph AI Agent — Capstone v2

A LangGraph-based AFL AI system with explicit routing between exact
statistical retrieval, leakage-safe match/player/season predictions, direct
AFL explanations, and off-topic refusal — with validation, clarification,
multi-turn memory, a FastAPI wrapper, structured logging, a comprehensive
evaluation suite, and a scope guard against prompt injection.

## What's new in v2 (this capstone pass)

- **Fixed a real multi-turn bug:** stale `player`/`team`/`stat`/`round`
  entities from an earlier, unrelated question no longer leak into later
  answers (e.g. asking about a player, then a completely different
  team-vs-team question, no longer silently keeps answering about the old
  player). Legitimate pronoun follow-ups ("his disposals", "that match")
  still work. Regression-tested in `eval_suite.py` and `e2e_conversations.py`.
- **New capabilities:** player-vs-player comparison, multi-season combined
  stats ("tackles across 2022 and 2023 combined"), single-game career/season
  highs (not silently substituted with an average), and generalized player
  stat predictions beyond disposals — goals, kicks, marks, handballs, tackles
  each have their own trained regressor.
- **Prompt-injection scope guard:** a dedicated, tested check
  (`router.is_prompt_injection_attempt`) blocks instruction-override
  attempts before they reach any classifier or tool.
- **Hardening:** every tool call runs through a hard timeout and never
  crashes the graph; every prediction response carries one consistent
  disclaimer, applied centrally.
- **FastAPI wrapper** (`api.py`) with structured JSON-line request logging
  and an explicit `/router-status` check to confirm an attached
  `GOOGLE_API_KEY` is actually being used.
- **Comprehensive evaluation suite** (`eval_suite.py`, 28 cases across 4
  categories) plus a naive-baseline comparison for the match model.

## Folder layout (zero configuration required)

```
afl-agent/
├── data/
│   ├── afl_match_retrieval.csv
│   ├── afl_player_retrieval.csv
│   ├── afl_match_prediction_features.csv
│   ├── afl_player_prediction_features.csv
│   └── feature_manifest.csv
├── artifacts/              <- created automatically by train.py
├── reports/                <- created automatically by generate_reports.py / build_executive_report.py
├── logs/                   <- created automatically by api.py (structured request logs)
├── .env                    <- optional, for GOOGLE_API_KEY (see below)
├── data_layer.py
├── resolvers.py
├── retrieval_tools.py
├── model_training.py
├── prediction_tools.py
├── state.py
├── router.py
├── entity_extraction.py
├── grounding.py
├── graph.py
├── api.py                  <- FastAPI wrapper
├── router_eval.py
├── eval_suite.py           <- Task 2 comprehensive evaluation
├── e2e_conversations.py
├── state_traces.py
├── generate_reports.py
├── build_executive_report.py
├── train.py
├── run.py
├── requirements.txt
└── README.md
```

## Setup

```bash
cd afl-agent
python -m venv venv
source venv/bin/activate   # venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## (Optional) Gemini API key

The agent works fully without an API key — it uses a deterministic
rule-based router by default (100% accuracy on the 34-query evaluation
suite). If you want the Gemini-based router instead, create a `.env` file:

```
GOOGLE_API_KEY=your-key-here
```

**Confirming the key is actually being used:** every `/chat` API response
includes `router_source: "gemini"` or `"rule_based"`. `GET /router-status`
runs a one-shot live check without needing a real conversation. If a key is
set but `router_source` keeps coming back `"rule_based"`, set
`AFL_ROUTER_DEBUG=1` before starting the server to print the real Gemini
exception to server logs instead of silently falling back.

**Never paste a real API key into a chat, terminal command, or anywhere
outside your own `.env` file** — if a key has ever been exposed, revoke it
and generate a new one.

## Train the models — do this once

```bash
python train.py
```

Trains and saves 8 models into `artifacts/`: match winner, player
top-disposals, and player expected-{disposals, goals, kicks, marks,
handballs, tackles} — all chronologically split by season (no shuffling).

## Chat with the agent (CLI)

```bash
python run.py
```

Type `debug` to toggle showing which router (gemini/rule_based) and
latency answered each turn.

Or from your own script:
```python
from graph import ask

r = ask("Who did Geelong play in Round 5 of 2020?", thread_id="session-1")
print(r["final_response"])

r = ask("Who had the most disposals for Geelong in that match?", thread_id="session-1")
print(r["final_response"])  # same thread_id -> context carries over correctly
```

## Run the API

```bash
uvicorn api:app --reload --port 8000
```

- `POST /chat` — `{"message": "...", "conversation_id": "..."}` → response + prediction metadata + `router_source`
- `GET /health` — liveness + whether models are trained + whether a key is configured
- `GET /router-status` — one-shot confirmation of whether an attached API key is actually answering
- Interactive docs at `http://localhost:8000/docs`

Every request is logged as one JSON line to `logs/requests.jsonl`
(timestamp, conversation_id, query, intent, tool_called, router_source,
validation_status, latency_ms, grounded) — this is what the monitoring
checklist (`reports/monitoring_checklist.md`) is built on.

## Generate submission deliverables

```bash
python generate_reports.py        # router_evaluation.md, evaluation_results.md,
                                   # end_to_end_conversations.md, state_traces.md,
                                   # model_training_report.md, submission_report.md
python build_executive_report.py  # reports/executive_report.pdf (2 pages)
```

`reports/monitoring_checklist.md` and `reports/demo_script.md` are
hand-authored deliverables (a checklist and a talk track aren't "run the
code and report the number" artifacts) but every number they cite is
pulled from the same real evaluation runs as everything else.

Every number in the generated files comes from actually running the real
code at generation time — nothing is hand-written or copy-pasted.

## What the agent can answer

| Query type | Example | How it's answered |
|---|---|---|
| Exact match/score retrieval | "Who did Geelong play in Round 5 of 2020?" | Pandas lookup on `afl_match_retrieval.csv` |
| Player stats (single season) | "What was Dangerfield's average disposals in 2020?" | Pandas lookup on `afl_player_retrieval.csv` |
| Player stats (multi-season combined) | "Dangerfield's tackles across 2022 and 2023 combined" | `get_player_multi_season_stats` |
| Single-game career/season high | "Nick Daicos's highest disposal game in 2023" | `get_player_single_game_high` (never a season average) |
| Player-vs-player comparison | "Sam Walsh vs Lachie Neale disposals in 2023" | `compare_players` |
| Head-to-head | "Carlton's win rate against Collingwood?" | Pandas lookup, broadened trigger phrases |
| Single-match winner prediction | "Who will win Cats vs Pies?" | Trained match-winner model |
| **Season/premiership prediction** | "Who will win AFL in 2030?" | Trained match-winner model as a full round-robin power ranking |
| Player stat prediction (any of 6 stats) | "Predict Geelong's top goalkicker" | Dedicated trained regressor per stat |
| Single-player point prediction | "How many disposals is Dangerfield expected to get?" | `predict_player_stat_value` |
| Direct AFL rules | "What is holding the ball?" | Curated glossary |
| Off-topic | "What's the offside rule in soccer?" | Polite refusal, redirected to AFL |
| Prompt injection | "Ignore previous instructions..." | Scope guard, refused before reaching any tool |
| Ambiguous team/player | "What was the score for Coast in round 1 2020?" | Asks for clarification — never guesses |

## Module map

| File | Purpose |
|---|---|
| `data_layer.py` | Cached CSV loading, manifest-driven safe-feature lists, auto-detects `data/` |
| `resolvers.py` | Team/player name resolution (never guesses on ambiguity) |
| `retrieval_tools.py` | 8 structured retrieval tools incl. comparison/multi-season/single-game-high |
| `model_training.py` | Trains + chronologically evaluates 8 models, auto-detects `artifacts/` |
| `prediction_tools.py` | Leakage-safe feature rebuilding + generalized multi-stat prediction wrappers |
| `state.py` | LangGraph `TypedDict` state schema (incl. player2/years for v2 features) |
| `router.py` | Intent classification (Gemini + deterministic fallback) + prompt-injection scope guard |
| `entity_extraction.py` | Conservative team/player/year candidate extraction (incl. multi-candidate) |
| `grounding.py` | Numeric grounding check for the response formatter |
| `graph.py` | All graph nodes + the entity-carryover bug fix + `build_graph()` / `ask()` |
| `api.py` | FastAPI wrapper with structured logging |
| `router_eval.py` | 34-query router accuracy evaluation + injection block-rate check |
| `eval_suite.py` | Task 2: 28-case combined evaluation across 4 categories + naive baseline comparison |
| `e2e_conversations.py` | 20 end-to-end conversation tests |
| `state_traces.py` | 6 annotated state-trace examples |
| `generate_reports.py` | Generates the Markdown deliverable files in `reports/` |
| `build_executive_report.py` | Generates `reports/executive_report.pdf` (2 pages) |
| `train.py` | Zero-config wrapper: trains all 8 models |
| `run.py` | Zero-config wrapper: interactive chatbot |

## Verified against the real data

- Feature-rebuild pipeline (`build_match_features_for_prediction`)
  reproduces the actual training file's feature definitions, verified
  against real historical matches.
- Router: 100% (34/34) accuracy; prompt-injection scope guard: 100% (6/6
  distinct injection styles blocked).
- Combined evaluation suite (`eval_suite.py`): 28/28 across factual QA,
  prediction sanity, scope guardrails, conversational coherence.
- Match winner model: logistic regression, test ROC-AUC ~0.71, test
  accuracy ~66% — beats both naive baselines (home-always-wins ~57%,
  career-win-rate favorite ~58%) by a real, honest margin.
- Player expected-disposals model: HistGradientBoosting, test MAE ~3.9.
  Five more regressors (goals, kicks, marks, handballs, tackles) trained
  the same way.
- Premiership power-ranking automatically excludes defunct/merged
  historical clubs (e.g. Fitzroy Lions, Brisbane Bears) by restricting to
  teams that played in the most recent 2 seasons of data.

## Known limitations

- No fixture/schedule file exists in the data, so single-match prediction
  never claims two teams are scheduled to play — it always discloses that
  it can't verify a fixture from local data.
- Season/premiership prediction is a power ranking from the single-match
  model, not a simulated ladder, fixture, or finals series — and becomes
  low-confidence the further the requested season is from the data's most
  recent season (flagged explicitly in the response).
- Venue-specific priors for a hypothetical fixture are left `NaN`
  (median-imputed) unless a real venue is passed in.
- "Eligible roster" for player predictions is approximated as players who
  appeared for the team in the most recent season present in the data.
- The rule-based router is regex/keyword-driven; unrecognized phrasings
  fall through to `ambiguous` rather than risk a misroute. Set
  `GOOGLE_API_KEY` for broader natural-language coverage via Gemini.
- The prompt-injection scope guard is marker-based — broad by design to
  minimize false negatives, but novel phrasings not resembling any known
  marker could still slip through (see `reports/monitoring_checklist.md`
  for the weekly review process that catches this in production).
