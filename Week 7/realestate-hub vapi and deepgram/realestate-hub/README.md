# RealEstate Hub — Week 7 Day 3: Voice Agent & Natural Conversation

This layer sits **on top of the completed Day-2 RAG project**. It does not replace the Day-2 SQL/vector pipeline.

## Architecture

```text
Browser microphone
      │  raw 16-bit PCM @ 16 kHz
      ▼
FastAPI WebSocket
      │
      ├── Google Gemini 3.5 Live Transcription streaming STT (`gemini-3.5-transcribe-live`)
      │       │
      │       └── final utterance
      ▼
Conversation Memory
  budget / city / locality / bedrooms / purpose + recent turns
      │
      ├── SQL recommendation engine for exact property constraints
      └── Chroma + Gemini embeddings for semantic facts
      ▼
Gemini conversational answer generation
      │
      ├── Pakistani UrduLish style
      ├── fillers / acknowledgements / hesitation
      ├── objection playbooks
      └── strict grounding / no fabricated facts
      ▼
ElevenLabs streaming TTS
      │
      ▼
Browser MediaSource audio playback

Barge-in: user speech / explicit interrupt cancels the active assistant turn.
Human evaluation: scores + notes + downloadable conversation log.
```

## APIs

- **Google Gemini Live Transcription**: real-time STT over the Live API. Audio is streamed as raw 16-bit PCM at 16 kHz, with interim and finalized transcription events. Automatic language detection supports multilingual/code-switched Urdu/English speech.
- **Google Gemini**: keeps the existing Day-2 RAG embeddings and LLM generation.
- **ElevenLabs**: streaming TTS using a low-latency Flash model. Choose an Urdu/Pakistani-capable voice from your account.

## Setup

1. Keep the knowledge base intact (`data/csv/`, `data/db/`, `data/vectorstores/`).
2. Fill in `.env` at the repo root:
   - `GOOGLE_API_KEY`
   - `ELEVENLABS_API_KEY`
   - `ELEVENLABS_VOICE_ID`
3. Install (from the repo root):

```bash
pip install -r requirements-day3.txt
```

4. Make sure the vector store exists. If not:

```bash
cd backend
python rebuild_vectorstore.py
```

5. Run the voice server (from `backend/`):

```bash
cd backend
uvicorn day3_voice_server:app --host 0.0.0.0 --port 8000
```

6. Start the Next.js frontend in a second terminal:

```bash
cd frontend
npm install
npm run dev
```

7. Open `http://localhost:3000` in Chrome/Edge and allow microphone access. The FastAPI backend remains available at `http://localhost:8000`.

## Project layout

```text
realestate-hub/
├── .env                     secrets (never commit)
├── backend/                 FastAPI server + RAG/voice-agent Python code
│   └── tests/
├── data/
│   ├── csv/                 knowledge-base CSVs (source of truth)
│   ├── db/                  SQLite snapshot
│   └── vectorstores/        persistent + eval Chroma collections
├── storage/
│   ├── recordings/          uploaded call recordings
│   └── evaluations/         saved human-evaluation records
├── results/                 generated eval CSVs (hallucination_eval.py, etc.)
├── database/postgres_version/
└── frontend/                 Next.js voice UI
```

All scripts (`build_knowledge_base.py`, `rebuild_vectorstore.py`, `health_check.py`, etc.) read/write these folders through the shared path constants in `backend/config.py` (`CSV_DIR`, `DB_DIR`, `VECTORSTORE_DIR`, `RECORDINGS_DIR`, `EVALUATIONS_DIR`, `RESULTS_DIR`) — run them from inside `backend/`.

## Required conversation tests

Try these in one session:

1. `Budget 3 crore hai.`
2. `DHA mein kya options hain?`
3. `Us se sasti koi option?`
4. `Thora mehnga lag raha hai.`
5. `Builder pe trust kaise karun?`
6. `Investment ke liye kaisa hai?`
7. Interrupt the assistant while it is speaking.
8. Speak in Urdu, then English, then UrduLish.

The server stores recent turns and extracted preferences so later requests do not need the customer to repeat the budget/locality.

## Latency target

The application measures turn latency from detected user speech start through Google STT and the assistant response becoming available, giving a more meaningful end-to-end voice latency measurement. It immediately sends a conversational acknowledgement before the RAG/LLM step and streams TTS audio as chunks arrive.

The **under-2-second target is measured, not falsely guaranteed**. Actual end-to-end latency depends on microphone/network RTT, Google Live transcription, Gemini response time, ElevenLabs time-to-first-byte, browser buffering, and the customer's hardware/network. Use the displayed latency and human evaluation form to verify the target on the deployment environment.

## Natural speech behaviors

The prompt supports controlled use of:

- `Ji bilkul...`
- `Acha...`
- `Hmm...`
- `Ek second...`
- short thinking pauses
- natural acknowledgements
- occasional context-appropriate soft laughter

The agent is instructed not to overuse these behaviors or laugh at objections/concerns.

## Human evaluation

The UI scores:

- Naturalness
- Persuasiveness
- Fluency
- Latency
- Conversation flow

It also stores evaluator notes and allows the live conversation transcript to be downloaded as JSON.

## Important production notes

- Never put API keys in frontend JavaScript.
- Use HTTPS/WSS in deployment.
- Store recordings/evaluations in object storage/database for production instead of the local `storage/recordings/` and `storage/evaluations/` folders.
- For real Pakistani sales quality, select or create an ElevenLabs voice that naturally handles Urdu/English code-switching and use representative human evaluation recordings.

---

## Day 4: Vapi-based voice server (`backend/vapi_voice_server.py`)

An alternative backend that hands the call over to [Vapi](https://vapi.ai) instead of driving
audio ourselves. **Only the LLM step is our own code** — STT and TTS are both Vapi-native
(Deepgram + Vapi's own hosted voice), and the RAG/objection-handling/memory logic in
`voice_agent.py` is reused as-is, unmodified, by both `day3_voice_server.py` and this file.

```text
Browser (Vapi's dashboard "Talk to Assistant", or the custom pages/vapi-voice.js)
      │
      ▼
Vapi's cloud (owns the call: mic capture, turn-taking, playback)
      │
      ├── transcribes the customer's audio using Vapi's native Deepgram
      │   integration (model "nova-3", language "ur" -- no server of ours,
      │   no separate Deepgram key, billed through Vapi like the voice is)
      │
      ├── chat turn  ──►  backend/vapi_voice_server.py: /vapi/llm/chat/completions (https)
      │                          │
      │                          ▼
      │                    voice_agent.make_voice_answer (unchanged RAG/memory/objection logic)
      │
      `── speaks the reply using Vapi's own hosted voice (provider "vapi", no API key of ours —
          swap VAPI_VOICE_PROVIDER/VAPI_VOICE_ID to "11labs"/"playht"/"azure"/etc. for a different
          voice through Vapi's own managed integration; Vapi still does the synthesis either way)
```

An earlier version of this routed STT through a custom websocket bridge to Google Live STT
(`/vapi/custom-transcriber`); that's been removed in favour of Vapi's native Deepgram transcriber,
which is simpler (no websocket bridge to maintain) and doesn't depend on an unverified custom
protocol. Deepgram's Nova-3 model supports Urdu as a dedicated monolingual model
(`language: "ur"`), which is why that's the default — Nova-3's auto-code-switching "multi" mode
covers English/Hindi/etc. but does **not** include Urdu, so it's not a safe default given this
project's UrduLish (Urdu + English) callers. Override `VAPI_TRANSCRIBER_MODEL`/
`VAPI_TRANSCRIBER_LANGUAGE` in `.env` to try `"multi"` instead if pure-Urdu accuracy turns out to
matter less than catching English terms than the "ur" mode + keyword-boosting handles.

### Setup

**Option A — Vapi's dashboard "Talk to Assistant" button (no frontend code, recommended for testing):**

1. Create a free account at [vapi.ai](https://vapi.ai) and copy your **private key**
   (Dashboard → API Keys → Private Key). This key is only ever used server-side, by
   `backend/create_vapi_assistant.py` — it never reaches a browser.
2. Fill in `.env` at the repo root (in addition to `GOOGLE_API_KEY`, still required — the LLM/RAG
   step still calls Gemini, only STT moved to Deepgram):
   - `VAPI_PRIVATE_KEY` — from step 1.
   - `BACKEND_PUBLIC_URL` — an `https://` URL Vapi's cloud can reach. **Not `localhost`.** In
     development, run `ngrok http 8000` and use the printed `https://...ngrok-free.app` URL.
   - `VAPI_TRANSCRIBER_MODEL` / `VAPI_TRANSCRIBER_LANGUAGE` — default `nova-3` / `ur`.
   - `VAPI_VOICE_PROVIDER` / `VAPI_VOICE_ID` — default `vapi` / `Elliot` (Vapi's own free hosted
     voice; zero extra keys).
3. Start the server (`cd backend && uvicorn vapi_voice_server:app --host 0.0.0.0 --port 8000`)
   and, in another terminal, `ngrok http 8000` — put its HTTPS URL into `BACKEND_PUBLIC_URL` in
   `.env` and restart the server (repeat each session, since free ngrok URLs rotate).
   Ngrok's free tier also serves an interstitial "you are about to visit..." warning page to any
   client that doesn't send an `ngrok-skip-browser-warning` header — including, potentially, Vapi's
   own calls to `/vapi/llm/chat/completions`, since we don't control Vapi's outgoing headers. If
   the assistant connects but never answers with anything sensible, this is the first thing to
   rule out (check ngrok's inspector at `http://127.0.0.1:4040` for what Vapi's requests actually
   received) — switching to a tunnel without this interstitial (e.g. Cloudflare Tunnel,
   `cloudflared tunnel --url http://localhost:8000`) removes the risk entirely.
4. Create the assistant:

   ```bash
   cd backend
   python create_vapi_assistant.py
   ```

   It prints a new assistant id — save it as `VAPI_ASSISTANT_ID` in `.env` so re-running the
   script later (e.g. after changing `BACKEND_PUBLIC_URL` for a new ngrok session, or any other
   setting) *updates* that same assistant instead of creating a duplicate:
   `python create_vapi_assistant.py $VAPI_ASSISTANT_ID`.
5. Open the [Vapi dashboard](https://dashboard.vapi.ai) → Assistants → your new assistant →
   **Talk to Assistant**. Speak — Vapi's Deepgram transcriber handles STT natively,
   `voice_agent.make_voice_answer` answers it (via `/vapi/llm/chat/completions`), and Vapi's own
   voice speaks the reply back.

**Option B — the custom Next.js page** (`frontend/pages/vapi-voice.js`), if you want your own
branded call UI instead of Vapi's dashboard:

1. Copy your **public key** instead (Dashboard → API Keys → Public Key) — no private key needed
   for this path, since the assistant is started inline via `vapi.start(config)` rather than
   pre-created through the REST API.
2. Fill in `.env`: `BACKEND_PUBLIC_URL`, `VAPI_TRANSCRIBER_MODEL`, `VAPI_TRANSCRIBER_LANGUAGE`,
   `VAPI_VOICE_PROVIDER`, `VAPI_VOICE_ID` (same as Option A, skip `VAPI_PRIVATE_KEY`).
3. Fill in `frontend/.env.local`: `NEXT_PUBLIC_VAPI_PUBLIC_KEY` — your public key.
4. Run the backend + ngrok as in Option A, then `cd frontend && npm run dev` and open
   `http://localhost:3000/vapi-voice`.

Either way, the original `http://localhost:3000/` still runs the unchanged Day-3
Google-STT-plus-ElevenLabs flow via `day3_voice_server.py`.

### What's different from Day 3

- **No `/api/recording` endpoint.** Vapi can record calls itself (`assistant.recordingEnabled` +
  `GET https://api.vapi.ai/call/:id` afterward); that requires the Vapi *private* key and the
  Server API, which is out of scope for this pass since it wasn't asked for. The Day-3 recording
  upload flow still works unchanged in `day3_voice_server.py`.
- **Streaming is pseudo-chunked, not token-by-token.** `make_voice_answer` returns its full answer
  in one call rather than streaming tokens from Gemini, so `/vapi/llm/chat/completions`'s SSE
  response is chunked into ~8-word pieces after the fact. It satisfies Vapi's streaming contract
  and lets Vapi start speaking slightly before the last chunk, but isn't true incremental
  generation. True streaming would mean restructuring `generate_answer.py`/`voice_agent.py` to
  stream from Gemini — a further change, not made here.
- **This project's execution environment can't reach `api.vapi.ai` or Google's API**, so the
  actual voice round-trip has only been verified by the person running this project, not tested
  end-to-end here. Everything up to those external calls — session/memory handling, the RAG
  pipeline, the SSE format, the assistant-config JSON shape — was tested directly and works.
