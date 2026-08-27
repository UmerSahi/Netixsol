# RealEstate Hub -- Knowledge Base, RAG & Property Intelligence
Single-agency AI knowledge base and retrieval pipeline (Week 7, Day 2).

## What changed in this version
- **Single agency, not a marketplace.** The multi-agency `agency` field was
  removed. Every listing now belongs to one company (RealEstate Hub) and is
  assigned to one of its 8 in-house agents (`agents.csv`).
- **Real developers dataset.** `developers.csv` now lists the actual
  housing authorities/developers (DHA, CDA, LDA, Bahria Town Pvt Ltd, etc.)
  that built/regulate each society -- meaningful for a single agency,
  unlike a list of competing brokerages.
- **Real Gemini embeddings + a real vector database.** `gemini-embedding-001`
  (via `langchain_google_genai.GoogleGenerativeAIEmbeddings`) generates the
  vectors, stored in a persistent `./chroma_db` ChromaDB collection
  (`properties`) via `langchain_chroma.Chroma` -- this replaced the earlier
  local TF-IDF+SVD embedder, which was a sandbox-network workaround, not a
  design choice. Retrieval is top-4 via `vectorstore.as_retriever(search_kwargs={"k": 4})`.
- **Real, live LLM answer generation.** `generate_answer.py` calls Google
  Gemini (`gemini-3.5-flash-lite` via `langchain_google_genai.ChatGoogleGenerativeAI`)
  directly with the retrieved, grounded context -- no templated/extractive
  stand-in.
- **Hallucination eval now judges the actual generated answer.** The
  previous version of `hallucination_eval.py` only checked whether
  retrieval found the right document/row -- it never looked at what Gemini
  actually said. It now runs the real pipeline end-to-end per question
  (retrieve -> generate -> judge) and grades the generated text itself with
  an LLM-as-judge (`judge_answer.py`, structured output via Pydantic) for
  Grounded/Hallucinated/Correct-Refusal, while keeping Retrieval Accuracy
  as a separate diagnostic so a bad score can be traced to "retrieval
  missed it" vs. "the LLM ignored good context."
  stand-in. Embeddings and generation are deliberately separate models/calls.
- **One `.env` file for everything.** Database backend (SQLite/Postgres)
  and the Google API key are both read from `.env` via `config.py`. Set it
  up once; never `export` anything in the terminal again.

## Setup (do this once)

```bash
pip install -r requirements.txt
cp .env.example .env
```

Open `.env` and fill in:
- `GOOGLE_API_KEY` -- get one free at https://aistudio.google.com/apikey
  (used for both the embedding model and the chat model below)
- `GEMINI_EMBEDDING_MODEL` -- defaults to `gemini-embedding-001`
- `GEMINI_LLM_MODEL` -- defaults to `gemini-3.5-flash-lite`; change if you use a different model
- `DB_BACKEND` -- `sqlite` (default, no server needed) or `postgres` (see `postgres_version/README_POSTGRES.md`)

## Run it

```bash
# 1. Build the knowledge base datasets + database (only needed once, or to
#    rebuild the CSVs/SQL from scratch)
python build_knowledge_base.py Property_with_Feature_Engineering.csv

# 2. Index the knowledge base into ChromaDB using gemini-embedding-001
#    (needs GOOGLE_API_KEY in .env -- makes real embedding API calls)
python rebuild_vectorstore.py

# 3. Structured (SQL) vs. semantic (vector) retrieval examples
python structured_retrieval.py

# 4. Recommendation engine
python recommendation_engine.py

# 5. Chunk-size evaluation (re-run against real embeddings; needs GOOGLE_API_KEY)
python chunk_size_eval.py

# 6. Hallucination evaluation -- generates and judges REAL Gemini answers
#    (retrieve -> generate -> LLM-judge per question; ~2 API calls/question)
python hallucination_eval.py

# 6b. Offline sanity check of the eval harness itself (no API key needed --
#     stubs the LLM/judge to prove the SQL/guardrail/metric logic is correct)
python test_hallucination_eval.py

# 7. Live, grounded answer generation via Gemini (needs GOOGLE_API_KEY in .env)
python generate_answer.py
```

Every script after step 1 reads `.env` automatically via `config.py` --
nothing to set in the terminal each session. Step 2 must run (and complete)
before steps 3, 5, 6, or 7 will find anything in `./chroma_db`.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Template -- copy to `.env` and fill in your real values |
| `config.py` | Loads `.env`; provides `get_engine()`, `get_embeddings()`, `get_llm()` |
| `build_knowledge_base.py` | Rebuilds all 9 datasets + database from the raw source CSV |
| `properties.csv`, `agents.csv`, `locations.csv`, `amenities.csv`, `schools.csv`, `hospitals.csv`, `developers.csv`, `payment_plans.csv`, `faqs.csv` | The 9 knowledge-base datasets |
| `realestate_kb.db` | SQLite snapshot (used when `DB_BACKEND=sqlite`) |
| `rag_pipeline.py` | Document loader, chunker, `gemini-embedding-001` embedder, ChromaDB vector store (`./chroma_db`), top-4 retriever |
| `rebuild_vectorstore.py` | Safe re-index script -- clears and rebuilds `./chroma_db` with the current embedding model |
| `generate_answer.py` | **Real, live Gemini call** (`gemini-3.5-flash-lite`) -- final grounded answer generation; also exposes `generate_answer_from_context()`, the core generation step reused by the eval |
| `judge_answer.py` | LLM-as-judge (structured Pydantic output) -- grades a generated answer against its verified context for Grounded/Hallucinated/Correct-Refusal |
| `structured_retrieval.py` | SQL vs. vector worked examples |
| `recommendation_engine.py` | Budget/city/bedroom/amenity recommender |
| `chunk_size_eval.py` | Chunk-size Hit@k evaluation (against real embeddings) |
| `hallucination_eval.py` | 20-question eval: retrieve -> generate -> judge, real Gemini calls throughout |
| `test_hallucination_eval.py` | Offline test of the eval harness logic using stub LLM/judge (no API key needed) |
| `postgres_version/README_POSTGRES.md` | Postgres-specific setup notes |

## What is genuinely real vs. a stated, documented constraint
- **Real:** the 750 filtered/sampled properties, the SQL layer, the
  `gemini-embedding-001` embeddings, the persistent ChromaDB vector store,
  the retrieval and guardrail logic, the live `gemini-3.5-flash-lite`
  answer-generation call, and the LLM-as-judge hallucination evaluation
  (`judge_answer.py`) that grades those real generated answers.
- **Documented, not hidden:** amenities and payment plans are illustrative
  dummy data (labeled as such); schools/hospitals are real named
  institutions but their listed distances are illustrative, not
  GPS-verified. `temperature=0` is passed to `gemini-3.5-flash-lite` for
  API-shape compatibility, but Google's current docs state this model
  deprecates and ignores sampling parameters -- determinism instead comes
  from the system prompt's explicit grounding rules. This sandbox has no
  network route to Google's API, so the live embedding/generation/judging
  calls could not be executed here; `hallucination_eval.py`'s logic was
  instead proven correct offline via `test_hallucination_eval.py` (stub
  LLM/judge functions, asserting the harness computes the right metrics
  given known inputs, and that it actually catches a fabricating model
  rather than always passing) -- run the real script locally with a
  `GOOGLE_API_KEY` for the actual evaluation numbers.
