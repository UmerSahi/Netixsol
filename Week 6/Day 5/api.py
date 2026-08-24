"""
api.py
======
Capstone Task 3: FastAPI wrapper around the LangGraph AFL agent.

Run:
    uvicorn api:app --reload --port 8000

Endpoints:
    POST /chat            -- main chat-style endpoint (message + conversation_id)
    GET  /health           -- liveness/readiness check (also reports whether
                               a GOOGLE_API_KEY is configured and models are
                               trained, so you can confirm the deployment is
                               wired correctly before sending traffic)
    GET  /router-status    -- explicit "is my API key actually being used?"
                               check: runs one real request through the
                               router and reports which path answered it
    GET  /logs/recent      -- last N structured log entries (for a quick
                               look without shelling into the box)

============================================================
"How do I know the response is coming through my attached API key?"
============================================================
Every /chat response includes a `router_source` field:
    - "gemini"      -> this specific answer was classified by the Gemini
                        LLM router (i.e. your GOOGLE_API_KEY was used)
    - "rule_based"  -> this specific answer used the deterministic
                        keyword/regex router (no key configured, OR the
                        Gemini call failed and fell back automatically --
                        router.py never crashes on a bad/missing key)
This is checked and logged PER REQUEST (not just once at startup), because
the graph falls back per-call, not just when the key is entirely absent --
so this is the only reliable way to confirm a given answer really used the
attached key. GET /router-status gives you a one-shot, dependency-free way
to check this without needing to inspect the full JSON of a normal chat call.

============================================================
Structured logging (Task 3)
============================================================
Every request appends ONE JSON line to logs/requests.jsonl containing:
    timestamp, conversation_id, query, intent, tool_called, router_source,
    validation_status, latency_ms, ok (bool), error (if any)
This is the foundation Task 4's monitoring checklist reads from.
"""
from __future__ import annotations
import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from graph import ask
from model_training import MODEL_DIR

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(
    title="AFL AI Agent API",
    description="Chat-style API over the LangGraph AFL assistant (retrieval, prediction, direct-AFL explanations).",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this to your actual frontend origin(s) before going live
    allow_methods=["*"],
    allow_headers=["*"],
)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LOG_DIR = Path(os.environ.get("AFL_LOG_DIR", os.path.join(_THIS_DIR, "logs")))
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "requests.jsonl"


def _log_request(entry: dict) -> None:
    """Append one structured JSON line. Logging failures never break a
    request -- this is observability, not a critical path."""
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="The user's message.")
    conversation_id: Optional[str] = Field(
        default=None,
        description="Stable ID for a multi-turn conversation. Omit to start a new one (one is generated and returned)."
    )


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    intent: str
    tool_called: Optional[str] = None
    validation_status: str
    router_source: str  # "gemini" | "rule_based" -- see module docstring
    latency_ms: float
    grounded: Optional[bool] = None
    prediction_metadata: Optional[dict] = None
    recent_games: Optional[list] = None


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    conversation_id = req.conversation_id or str(uuid.uuid4())
    t0 = time.monotonic()
    error = None
    try:
        result = ask(req.message, thread_id=conversation_id)
    except Exception as e:  # hardening: the API layer never 500s opaquely
        error = str(e)
        traceback.print_exc()
        _log_request({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "conversation_id": conversation_id, "query": req.message,
            "ok": False, "error": error,
            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        })
        raise HTTPException(status_code=500, detail=f"Internal error while answering: {error}")

    tool_result = result.get("tool_result") or {}
    prediction_metadata = None
    recent_games = None
    if result.get("tool_called") == "get_team_recent_stats" and tool_result.get("ok"):
        recent_games = (tool_result.get("data") or {}).get("matches")
    if result.get("intent", "").startswith("prediction") and tool_result.get("ok"):
        data = tool_result.get("data") or {}
        prediction_metadata = {
            "is_prediction": True,
            **{k: v for k, v in data.items()
               if k in ("model_type", "model_val_auc", "model_test_auc", "model_val_mae", "model_test_mae",
                        "predicted_winner", "probability_home_win", "probability_away_win",
                                "home_team", "away_team", "season", "confidence_note", "fixture_note", "method_note",
                                "prediction_type", "predictions")}
        }

    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "conversation_id": conversation_id,
        "query": req.message,
        "intent": result.get("intent"),
        "tool_called": result.get("tool_called"),
        "router_source": result.get("router_source"),
        "validation_status": result.get("validation_status"),
        "latency_ms": result.get("latency_ms"),
        "grounded": (result.get("grounding_check") or {}).get("grounded"),
        "ok": tool_result.get("ok", True),
        "error": None if tool_result.get("ok", True) else tool_result.get("error") or tool_result.get("clarification"),
    }
    _log_request(log_entry)

    return ChatResponse(
        response=result["final_response"],
        conversation_id=conversation_id,
        intent=result.get("intent", "unknown"),
        tool_called=result.get("tool_called"),
        validation_status=result.get("validation_status", "unknown"),
        router_source=result.get("router_source", "rule_based"),
        latency_ms=result.get("latency_ms", 0.0),
        grounded=(result.get("grounding_check") or {}).get("grounded"),
        prediction_metadata=prediction_metadata,
        recent_games=recent_games,
    )


@app.get("/health")
def health() -> dict:
    api_key_configured = bool(os.environ.get("GOOGLE_API_KEY"))
    models_trained = os.path.exists(os.path.join(MODEL_DIR, "match_winner_metadata.json"))
    try:
        from data_layer import DATA_DIR, _FILES
        data_ok = all(os.path.exists(os.path.join(DATA_DIR, _FILES[k]))
                      for k in ("match_retrieval", "player_retrieval", "match_features", "player_features"))
    except Exception:
        data_ok = False
    return {
        "status": "ok" if (data_ok and models_trained) else "degraded",
        "data_loaded": data_ok,
        "models_trained": models_trained,
        "google_api_key_configured": api_key_configured,
        "router_mode": "gemini (with rule-based fallback)" if api_key_configured else "rule_based only",
    }


@app.get("/router-status")
def router_status() -> dict:
    """One-shot check: sends a real, harmless query through the router and
    reports which path answered it -- the definitive way to confirm an
    attached GOOGLE_API_KEY is actually being used right now, as opposed to
    just being present in the environment (a bad key, quota error, or
    network issue would still silently fall back to rule_based -- this
    endpoint surfaces that instead of hiding it)."""
    api_key_configured = bool(os.environ.get("GOOGLE_API_KEY"))
    probe_query = "Who did Geelong play in Round 5 of 2020?"
    result = ask(probe_query, thread_id=f"router-status-probe-{uuid.uuid4()}")
    return {
        "google_api_key_configured": api_key_configured,
        "probe_query": probe_query,
        "router_source_used": result.get("router_source"),
        "interpretation": (
            "Your GOOGLE_API_KEY is configured AND was actually used for this request."
            if result.get("router_source") == "gemini" else
            "No API key is configured, OR the Gemini call failed/fell back -- "
            "this request was answered by the deterministic rule-based router. "
            "Set AFL_ROUTER_DEBUG=1 and check server logs for the underlying error if "
            "you expected the key to be used."
        ),
    }


@app.get("/logs/recent")
def recent_logs(n: int = 50) -> dict:
    if not _LOG_FILE.exists():
        return {"entries": []}
    with open(_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    entries = [json.loads(l) for l in lines if l.strip()]
    return {"entries": entries, "count": len(entries)}


# Keep these mounts after the API routes so the root UI does not shadow them.
app.mount("/bg image", StaticFiles(directory=os.path.join(_THIS_DIR, "bg image")), name="background-assets")
app.mount("/", StaticFiles(directory=os.path.join(_THIS_DIR, "ui"), html=True), name="ui")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
