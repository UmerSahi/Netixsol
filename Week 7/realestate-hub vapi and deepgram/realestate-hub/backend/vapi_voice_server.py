"""Day 4: Vapi-based voice server.

Replaces the Day-3 architecture (browser <-> our own /ws/voice websocket <->
Google Live STT + ElevenLabs) with Vapi owning the call end to end:

    Browser (Vapi Web SDK, or Vapi's dashboard "Talk to Assistant")
        |
        v
    Vapi's cloud
        |
        |---- transcribes the customer's audio using Vapi's own native
        |     Deepgram integration (no webhook, no key of ours)
        |
        |---- chat completion request ------------->  /vapi/llm/chat/completions (this file)
        |                                                 |
        |                                                 v
        |                                          voice_agent.make_voice_answer (RAG, unchanged)
        |
        `---- speaks the reply using Vapi's own hosted TTS voice (no webhook, no ElevenLabs key)

STT was originally a custom Google Live STT bridge (a websocket at
/vapi/custom-transcriber); that's been removed in favour of Vapi's native
Deepgram transcriber, which needs no server of ours at all and no separate
Deepgram key (billed through Vapi like the voice is). Only the LLM step still
calls out to our backend -- the RAG/objection-handling/memory logic in
voice_agent.py is reused as-is.

Run with:  uvicorn vapi_voice_server:app --host 0.0.0.0 --port 8000
Requires BACKEND_PUBLIC_URL (an https URL Vapi's servers can reach -- ngrok
in dev, your real domain in production) for /vapi/assistant-config.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.request
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.requests import Request

from config import EVALUATIONS_DIR
from voice_agent import ConversationMemory, make_voice_answer, stream_sink_context

# Vapi's native Deepgram transcriber -- no server of ours, no separate
# Deepgram key needed. Nova-3 supports Urdu as a dedicated monolingual model
# ("ur"), which is the more accurate choice for mostly-Urdu speech; Nova-3's
# auto-code-switching "multi" mode covers English/Hindi/etc. but does NOT
# include Urdu, so it's the riskier pick for this project's UrduLish callers.
# Override via .env if you want to test the alternative.
VAPI_TRANSCRIBER_MODEL = os.getenv("VAPI_TRANSCRIBER_MODEL", "nova-3")
VAPI_TRANSCRIBER_LANGUAGE = os.getenv("VAPI_TRANSCRIBER_LANGUAGE", "ur")
# Boosts recognition of these terms even while transcribing in Urdu mode.
# nova-3 uses Deepgram's newer "Keyterm Prompting" (plain phrases, no
# intensifier syntax) rather than the legacy "keywords" field (single words
# only, e.g. "word" or "word:2") -- keyterm accepts full phrases like
# "Bahria Town" directly, which is why it's used here instead.
TRANSCRIBER_KEYTERMS = [
    "DHA", "Bahria Town", "Gulberg", "Johar Town", "Model Town",
    "Islamabad", "Lahore", "Rawalpindi", "PKR", "crore", "lakh",
    "RealEstate Hub",
]

# Vapi-native voice: no API key of ours is needed -- Vapi synthesizes and
# streams the audio itself. Swap VAPI_VOICE_PROVIDER to "11labs", "playht",
# "azure", "cartesia", "rime", or "deepgram" (with a matching voiceId) if you
# want a specific paid voice through Vapi's own managed integration instead
# of Vapi's built-in voices -- either way Vapi still does the TTS call, not us.
VAPI_VOICE_PROVIDER = os.getenv("VAPI_VOICE_PROVIDER", "vapi")
VAPI_VOICE_ID = os.getenv("VAPI_VOICE_ID", "Elliot")
VAPI_ASSISTANT_NAME = os.getenv("VAPI_ASSISTANT_NAME", "RealEstate Hub Voice Agent")
VAPI_LLM_MODEL_LABEL = os.getenv("GEMINI_LLM_MODEL", "gemini-3.5-flash-lite")
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "").strip().rstrip("/")
FIRST_MESSAGE = os.getenv("VAPI_FIRST_MESSAGE", "").strip() or (
    "Assalam-o-Alaikum! RealEstate Hub mein khush aamdeed. Main aap ka property consultant hoon. "
    "Bataiye, aap ghar dekh rahe hain, flat ya plot?"
)

app = FastAPI(title="RealEstate Hub Day 4 Voice Agent (Vapi)")

FRONTEND_ORIGINS = [x.strip() for x in os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
).split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Keyed by Vapi's call id (falls back to a synthetic key) so preferences
# gathered by ConversationMemory persist across turns of the same call.
SESSIONS: dict[str, ConversationMemory] = {}
EVAL_DIR = EVALUATIONS_DIR
EVAL_DIR.mkdir(exist_ok=True, parents=True)


@app.get("/")
async def index():
    return {"service": "RealEstate Hub Day 4 Voice Agent", "voice_backend": "vapi"}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "google_configured": bool(os.getenv("GOOGLE_API_KEY")),  # still needed for the RAG/LLM step
        "backend_public_url_configured": bool(BACKEND_PUBLIC_URL),
        "vapi_voice_provider": VAPI_VOICE_PROVIDER,
        "vapi_voice_id": VAPI_VOICE_ID,
        "transcriber_provider": "deepgram (native, via Vapi)",
        "transcriber_model": VAPI_TRANSCRIBER_MODEL,
        "transcriber_language": VAPI_TRANSCRIBER_LANGUAGE,
    }


def get_active_ngrok_url() -> str | None:
    """Auto-detect public URL from local ngrok client (port 4040) if running."""
    try:
        req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels", headers={"User-Agent": "realestate-hub/1.0"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for t in data.get("tunnels", []):
                if t.get("proto") == "https" and t.get("public_url"):
                    return str(t["public_url"]).strip().rstrip("/")
    except Exception:
        pass
    return None


def get_backend_public_url() -> str:
    """Return the active public URL: ngrok local API if active, else .env."""
    ngrok_url = get_active_ngrok_url()
    if ngrok_url:
        return ngrok_url
    return os.getenv("BACKEND_PUBLIC_URL", "").strip().rstrip("/")


def build_assistant_config(public_url: str | None = None) -> dict:
    """Build the assistant config shared by `/vapi/assistant-config` (inline
    `vapi.start(config)` usage) and `create_vapi_assistant.py` (persisted via
    Vapi's REST API using the private key)."""
    base_url = (public_url or get_backend_public_url()).rstrip("/")
    return {
        "name": VAPI_ASSISTANT_NAME,
        "firstMessage": FIRST_MESSAGE,
        "model": {
            "provider": "custom-llm",
            # Vapi treats this as an OpenAI base URL and appends /chat/completions itself.
            "url": f"{base_url}/vapi/llm",
            "model": VAPI_LLM_MODEL_LABEL,
            "messages": [{"role": "system", "content": "(handled server-side by voice_agent.make_voice_answer)"}],
        },
        "transcriber": {
            "provider": "deepgram",
            "model": VAPI_TRANSCRIBER_MODEL,
            "language": VAPI_TRANSCRIBER_LANGUAGE,
            "smartFormat": True,
            "keyterm": TRANSCRIBER_KEYTERMS,
        },
        "voice": {
            "provider": VAPI_VOICE_PROVIDER,
            "voiceId": VAPI_VOICE_ID,
        },
        "clientMessages": ["transcript", "conversation-update", "speech-update"],
    }


@app.get("/vapi/assistant-config")
async def vapi_assistant_config():
    """Inline assistant config for the frontend's `vapi.start(config)` call."""
    url = get_backend_public_url()
    if not url:
        return JSONResponse(
            {"error": "BACKEND_PUBLIC_URL is not set in .env and no local ngrok tunnel was detected. "
                      "It must be an https URL Vapi's cloud can reach."},
            status_code=500,
        )
    return build_assistant_config(public_url=url)


# ---------------------------------------------------------------------------
# Custom LLM (OpenAI-compatible) -- wraps voice_agent.make_voice_answer
# ---------------------------------------------------------------------------

def _openai_chunk(
    model: str,
    content: str | None = None,
    finish_reason: str | None = None,
    completion_id: str | None = None,
    created: int | None = None,
    include_role: bool = False,
) -> dict:
    cid = completion_id or f"chatcmpl-{uuid.uuid4().hex[:24]}"
    cts = created if created is not None else int(time.time())
    delta: dict[str, Any] = {}
    if include_role:
        delta["role"] = "assistant"
    if content is not None:
        delta["content"] = content
    return {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": cts,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }


async def _sse_stream(answer: str, model: str):
    """Pseudo-chunked SSE: used only for already-complete short strings
    (e.g. voice_agent's scripted/instant responses, or the "Ji, boliye."
    filler below) where there's no real generation latency to hide."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())
    # Immediate role chunk for instant TTFB
    yield f"data: {json.dumps(_openai_chunk(model, None, None, completion_id, created_ts, include_role=True))}\n\n"
    words = answer.split(" ")
    step = 8
    for i in range(0, len(words), step):
        piece = " ".join(words[i:i + step])
        if i + step < len(words):
            piece += " "
        yield f"data: {json.dumps(_openai_chunk(model, piece, None, completion_id, created_ts))}\n\n"
        await asyncio.sleep(0)
    yield f"data: {json.dumps(_openai_chunk(model, None, 'stop', completion_id, created_ts))}\n\n"
    yield "data: [DONE]\n\n"


_SENTINEL = object()


async def _sse_stream_live(user_text: str, memory: ConversationMemory, model: str):
    """Deadlock-free, resilient SSE streaming for Vapi custom LLM.

    Emits an instant role chunk (<1ms) to satisfy Vapi's TTFB requirement,
    generates the verified answer with timeout protection, and streams words
    cleanly according to the OpenAI SSE specification.
    """
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created_ts = int(time.time())

    # 1. Immediate role chunk so Vapi receives response in <1ms
    yield f"data: {json.dumps(_openai_chunk(model, None, None, completion_id, created_ts, include_role=True))}\n\n"

    # 2. Generate answer with timeout protection
    try:
        answer, _meta = await asyncio.wait_for(
            asyncio.to_thread(make_voice_answer, user_text, memory),
            timeout=12.0
        )
    except asyncio.TimeoutError:
        answer = "Ji sir, main system check kar raha hoon. Aap kis city mein property dekhna chahenge?"
    except Exception as e:
        answer = "Ji sir, hamare paas Lahore, Islamabad aur Rawalpindi mein options available hain."

    memory.add("assistant", answer)

    # 3. Stream text chunks smoothly
    words = answer.split(" ")
    for i, word in enumerate(words):
        space = " " if i < len(words) - 1 else ""
        yield f"data: {json.dumps(_openai_chunk(model, word + space, None, completion_id, created_ts))}\n\n"
        if i % 4 == 0:
            await asyncio.sleep(0.01)

    # 4. Standard completion stop chunk and [DONE]
    yield f"data: {json.dumps(_openai_chunk(model, None, 'stop', completion_id, created_ts))}\n\n"
    yield "data: [DONE]\n\n"


def _openai_completion(answer: str, model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@app.post("/vapi/llm/chat/completions")
async def vapi_llm_chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model_label = body.get("model") or VAPI_LLM_MODEL_LABEL
    call_id = ((body.get("call") or {}).get("id")) or "default"
    session_id = f"vapi:{call_id}"
    memory = SESSIONS.setdefault(session_id, ConversationMemory())

    user_messages = [m for m in messages if m.get("role") == "user"]
    latest_user_text = str(user_messages[-1].get("content", "")).strip() if user_messages else ""

    is_new_turn = bool(latest_user_text) and (
        not memory.turns or memory.turns[-1].get("role") != "user" or memory.turns[-1].get("text") != latest_user_text
    )

    if not is_new_turn:
        # No new customer utterance (e.g. Vapi warming up the connection).
        answer = "Ji, boliye."
        if body.get("stream"):
            return StreamingResponse(_sse_stream(answer, model_label), media_type="text/event-stream", headers=SSE_HEADERS)
        return JSONResponse(_openai_completion(answer, model_label))

    memory.add("user", latest_user_text)

    if body.get("stream"):
        return StreamingResponse(_sse_stream_live(latest_user_text, memory, model_label), media_type="text/event-stream", headers=SSE_HEADERS)

    answer, _meta = await asyncio.to_thread(make_voice_answer, latest_user_text, memory)
    memory.add("assistant", answer)
    return JSONResponse(_openai_completion(answer, model_label))


# ---------------------------------------------------------------------------
# Human-eval endpoints (unchanged from Day 3 -- not Vapi/ElevenLabs specific)
# ---------------------------------------------------------------------------

@app.post("/api/chat")
async def chat(payload: dict[str, Any]):
    """Text-only endpoint for testing the RAG agent without a voice call."""
    text = str(payload.get("text", "")).strip()
    if not text:
        return JSONResponse({"ok": False, "error": "Message cannot be empty."}, status_code=400)
    session_id = str(payload.get("session_id") or uuid.uuid4().hex)
    memory = SESSIONS.setdefault(session_id, ConversationMemory())
    memory.add("user", text)
    answer, meta = await asyncio.to_thread(make_voice_answer, text, memory)
    memory.add("assistant", answer)
    return {
        "ok": True,
        "session_id": session_id,
        "text": answer,
        "objection": meta.get("objection"),
        "sources": meta.get("sources", []),
        "memory": memory.preferences,
    }


@app.post("/api/evaluation")
async def save_evaluation(payload: dict[str, Any]):
    evaluation_id = uuid.uuid4().hex
    payload["evaluation_id"] = evaluation_id
    payload["saved_at"] = time.time()
    (EVAL_DIR / f"{evaluation_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "evaluation_id": evaluation_id}


@app.get("/api/evaluations")
async def list_evaluations():
    rows = []
    for p in sorted(EVAL_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


# ---------------------------------------------------------------------------
# Day 4: Appointments, Calendar, Email & CRM Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/appointments")
async def create_appointment_api(payload: dict[str, Any]):
    """Book a new appointment across Calendar, Email, and CRM."""
    from appointment_manager import book_appointment
    result = await asyncio.to_thread(
        book_appointment,
        client_name=payload.get("client_name", "Valued Client"),
        client_phone=payload.get("client_phone", "Not Provided"),
        client_email=payload.get("client_email", ""),
        employee_name=payload.get("employee_name") or payload.get("agent_name") or "Ahmed Raza",
        property_title=payload.get("property_title", "Site Visit"),
        property_id=payload.get("property_id", "PROP-General"),
        date_str=payload.get("date_str", "Tomorrow"),
        time_str=payload.get("time_str", "3:00 PM"),
        city=payload.get("city", ""),
        budget=payload.get("budget", ""),
        property_type=payload.get("property_type", "House"),
        purpose=payload.get("purpose", "For Sale"),
        requirements=payload.get("requirements", ""),
        notes=payload.get("notes", "Scheduled via API"),
    )
    return result


@app.post("/api/appointments/{appointment_id}/reschedule")
async def reschedule_appointment_api(appointment_id: str, payload: dict[str, Any]):
    """Reschedule an existing appointment."""
    from appointment_manager import reschedule_appointment
    result = await asyncio.to_thread(
        reschedule_appointment,
        appointment_id=appointment_id,
        new_date_str=payload.get("new_date_str") or payload.get("date_str", "Tomorrow"),
        new_time_str=payload.get("new_time_str") or payload.get("time_str", "5:00 PM"),
        reason=payload.get("reason", "Rescheduled via API"),
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@app.post("/api/appointments/{appointment_id}/cancel")
async def cancel_appointment_api(appointment_id: str, payload: dict[str, Any]):
    """Cancel an existing appointment."""
    from appointment_manager import cancel_appointment
    result = await asyncio.to_thread(
        cancel_appointment,
        appointment_id=appointment_id,
        reason=payload.get("reason", "Cancelled by client"),
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return result


@app.get("/api/appointments")
async def list_appointments_api(status: str | None = None, limit: int = 50):
    """List CRM appointments."""
    from crm_service import list_appointments
    rows = await asyncio.to_thread(list_appointments, limit=limit, status=status)
    return rows


@app.get("/api/appointments/{appointment_id}")
async def get_appointment_api(appointment_id: str):
    """Get single appointment details."""
    from crm_service import get_appointment
    row = await asyncio.to_thread(get_appointment, appointment_id=appointment_id)
    if not row:
        return JSONResponse({"ok": False, "error": "Appointment not found"}, status_code=404)
    return row


@app.get("/api/crm/leads")
async def list_leads_api(limit: int = 50):
    """List CRM client leads and saved preferences."""
    from crm_service import list_leads
    return await asyncio.to_thread(list_leads, limit=limit)


@app.get("/api/crm/transcripts")
async def list_transcripts_api(limit: int = 50):
    """List call transcripts."""
    from crm_service import list_transcripts
    return await asyncio.to_thread(list_transcripts, limit=limit)


@app.get("/api/crm/reminders")
async def list_reminders_api(status: str = "pending", limit: int = 50):
    """List follow-up reminders."""
    from crm_service import list_reminders
    return await asyncio.to_thread(list_reminders, limit=limit, status=status)


@app.post("/api/crm/reminders/{reminder_id}/dismiss")
async def dismiss_reminder_api(reminder_id: str):
    """Dismiss a reminder."""
    from crm_service import dismiss_reminder
    await asyncio.to_thread(dismiss_reminder, reminder_id=reminder_id)
    return {"ok": True, "reminder_id": reminder_id}


@app.get("/api/crm/dashboard")
async def get_crm_dashboard_api():
    """Get overview stats for CRM dashboard."""
    from crm_service import get_crm_dashboard_stats
    return await asyncio.to_thread(get_crm_dashboard_stats)


@app.get("/api/emails")
async def list_emails_api(limit: int = 50):
    """List dispatched employee notification emails."""
    from email_service import list_sent_emails
    return await asyncio.to_thread(list_sent_emails, limit=limit)


@app.post("/api/webhooks/vapi-call-ended")
async def vapi_call_ended_webhook(payload: dict[str, Any]):
    """Vapi Server Webhook: logs call transcript and updates lead CRM upon call completion."""
    from crm_service import log_call_transcript, upsert_lead

    message = payload.get("message", {}) or payload
    call = message.get("call", {}) or payload.get("call", {})
    call_id = call.get("id") or str(uuid.uuid4().hex[:8])
    customer = call.get("customer", {})
    caller_phone = customer.get("number") or payload.get("phone", "Anonymous")

    transcript = message.get("transcript") or payload.get("transcript", "")
    summary = message.get("summary") or payload.get("summary", "Voice Agent Call Completed")
    turns = message.get("messages") or payload.get("turns", [])

    # Record transcript in CRM
    tr_id = await asyncio.to_thread(
        log_call_transcript,
        call_id=call_id,
        caller_phone=caller_phone,
        transcript_text=transcript,
        summary=summary,
        sentiment="Positive",
        turns=turns,
    )

    # Upsert lead if phone is known
    if caller_phone and caller_phone != "Anonymous":
        await asyncio.to_thread(
            upsert_lead,
            client_name="Call Inquirer",
            phone=caller_phone,
            stage="Qualified",
            preferences={"last_call_id": call_id, "summary": summary},
        )

    return {"ok": True, "transcript_id": tr_id, "call_id": call_id}