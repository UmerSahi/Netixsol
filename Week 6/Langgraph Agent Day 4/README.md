# AFL LangGraph AI Agent

A LangGraph-based AFL AI system with explicit routing between exact
statistical retrieval, leakage-safe match/player/season predictions, direct
AFL explanations, and off-topic refusal — with validation, clarification,
and multi-turn memory.

## Folder layout (zero configuration required)

Put everything in one folder, exactly like this:

```
langgraph agent/
├── data/
│   ├── afl_match_retrieval.csv
│   ├── afl_player_retrieval.csv
│   ├── afl_match_prediction_features.csv
│   ├── afl_player_prediction_features.csv
│   └── feature_manifest.csv
├── artifacts/              <- created automatically by train.py
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
├── router_eval.py
├── e2e_conversations.py
├── state_traces.py
├── generate_reports.py
├── train.py
├── run.py
├── requirements.txt
└── README.md
```

**As long as your CSVs are in a `data/` subfolder next to the `.py` files
(as shown), nothing needs to be set, exported, or configured.** Paths
auto-detect relative to the code's own location.

## Setup

```powershell
cd "langgraph agent"
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

## (Optional) Gemini API key

The agent works fully without an API key — it uses a deterministic
rule-based router by default (100% accuracy on the 21-query evaluation
suite). If you want the Gemini-based router instead, create a `.env` file
in this folder:

```
GOOGLE_API_KEY=your-key-here
```

`run.py` loads it automatically via `python-dotenv`. **Never paste a real
API key into a chat, terminal command, or anywhere outside your own `.env`
file** — if a key has ever been exposed, revoke it and generate a new one.

## Train the models — do this once

```powershell
python train.py
```

Trains and saves 3 models into `artifacts/`: match winner, player
top-disposals, and player expected-disposals — all chronologically split
by season (no shuffling).

## Chat with the agent

```powershell
python run.py
```

Try:
```
Who did Geelong play in Round 5 of 2020?
Who will win Cats vs Pies?
Who is most likely to lead Geelong in disposals?
Who will win AFL in 2030?
```

Or from your own script:
```python
from graph import ask

r = ask("Who did Geelong play in Round 5 of 2020?", thread_id="session-1")
print(r["final_response"])

r = ask("Who had the most disposals for Geelong in that match?", thread_id="session-1")
print(r["final_response"])  # same thread_id -> context carries over
```

## Generate submission deliverables (Markdown)

```powershell
python generate_reports.py
```

Writes into `reports/`:
- `router_evaluation.md` — 21-query routing accuracy table
- `end_to_end_conversations.md` — 12 real conversations through the graph
- `state_traces.md` — 4 annotated state traces
- `model_training_report.md` — trained model metrics (only if `train.py` has been run)
- `submission_report.md` — all of the above combined into one document

Every number in these files comes from actually running the real code at
generation time — nothing is hand-written or copy-pasted from an earlier run.

## What the agent can answer

| Query type | Example | How it's answered |
|---|---|---|
| Exact match/score retrieval | "Who did Geelong play in Round 5 of 2020?" | Pandas lookup on `afl_match_retrieval.csv` |
| Player stats | "What was Dangerfield's average disposals in 2020?" | Pandas lookup on `afl_player_retrieval.csv` |
| Head-to-head | "Geelong's record against Collingwood?" | Pandas lookup |
| Single-match winner prediction | "Who will win Cats vs Pies?" | Trained match-winner model |
| **Season/premiership prediction** | "Who will win AFL in 2030?" | Trained match-winner model run as a full round-robin power ranking across all currently active teams |
| Player prediction | "Who is most likely to lead Geelong in disposals?" | Trained player models |
| Direct AFL rules | "What is holding the ball?" | Curated glossary |
| Off-topic | "What's the offside rule in soccer?" | Polite refusal, redirected to AFL |
| Ambiguous team/player | "What was the score for Coast in round 1 2020?" | Asks for clarification — never guesses |

**Note on season/premiership predictions**: there is no separate
"championship model" and no fixture/ladder/finals simulation — the same
match-winner model is applied to a full round-robin of every active team
vs every other team, using each team's most recent known form, and ranked
by average predicted win probability. For years more than 1 season beyond
the data's most recent season, the response explicitly flags this as a
low-confidence extrapolation, since rosters and team strength change
substantially year to year and no real fixture exists for a future season.

## Module map

| File | Purpose |
|---|---|
| `data_layer.py` | Cached CSV loading, manifest-driven safe-feature lists, auto-detects `data/` |
| `resolvers.py` | Team/player name resolution (never guesses on ambiguity) |
| `retrieval_tools.py` | 5 structured retrieval tools (exact data, no LLM memory) |
| `model_training.py` | Trains + chronologically evaluates the 3 prediction models, auto-detects `artifacts/` |
| `prediction_tools.py` | Leakage-safe feature rebuilding + prediction wrappers, incl. season/premiership ranking |
| `state.py` | LangGraph `TypedDict` state schema |
| `router.py` | Intent classification (Gemini + deterministic fallback) |
| `entity_extraction.py` | Conservative team/player candidate extraction |
| `grounding.py` | Numeric grounding check for the response formatter |
| `graph.py` | All graph nodes + `build_graph()` / `ask()` |
| `router_eval.py` | 21-query router accuracy evaluation |
| `e2e_conversations.py` | 12 end-to-end conversation tests |
| `state_traces.py` | 4 annotated state-trace examples |
| `generate_reports.py` | Generates the Markdown deliverable files in `reports/` |
| `train.py` | Zero-config wrapper: trains all 3 models |
| `run.py` | Zero-config wrapper: interactive chatbot |

## Verified against the real data

- Feature-rebuild pipeline (`build_match_features_for_prediction`)
  reproduces the actual training file exactly: 0/68 column mismatches
  across 4 real historical matches spanning 1983–2025.
- Router: 100% (21/21) accuracy.
- Match winner model: logistic regression, test ROC-AUC 0.714.
- Player top-disposals model: random forest, test ROC-AUC 0.895.
- Player expected-disposals model: HistGradientBoosting, test MAE ~3.9.
- Premiership power-ranking automatically excludes defunct/merged
  historical clubs (e.g. Fitzroy Lions, Brisbane Bears) by restricting to
  teams that played in the most recent 2 seasons of data — without this,
  clubs with tiny, decades-stale samples produced nonsensical near-100%
  win probabilities.

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
