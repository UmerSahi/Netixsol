from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from websockets.exceptions import ConnectionClosed
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from config import PROJECT_ROOT
import websockets
from voice_agent import ConversationMemory, make_voice_answer

from fastapi.responses import JSONResponse

load_dotenv(PROJECT_ROOT / ".env")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
GOOGLE_STT_MODEL = os.getenv("GOOGLE_STT_MODEL", "gemini-3.5-transcribe-live")
GOOGLE_STT_LANGUAGE_CODES = [x.strip() for x in os.getenv("GOOGLE_STT_LANGUAGE_CODES", "").split(",") if x.strip()]
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")

app = FastAPI(title="RealEstate Hub Day 3 Voice Agent")

# The Next.js frontend (pages/index.js) runs on a separate origin/port during
# development, so the old same-origin assumption from the plain static/app.js
# frontend no longer holds. Allow the local Next.js dev server explicitly
# rather than "*", since the WebSocket/API calls carry API-backed data.
FRONTEND_ORIGINS = [x.strip() for x in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001").split(",") if x.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS: dict[str, ConversationMemory] = {}
EVAL_DIR = PROJECT_ROOT / "evaluations"
REC_DIR = PROJECT_ROOT / "recordings"
EVAL_DIR.mkdir(exist_ok=True)
REC_DIR.mkdir(exist_ok=True)


def require_keys() -> None:
    missing = [k for k, v in (("GOOGLE_API_KEY", os.getenv("GOOGLE_API_KEY")), ("ELEVENLABS_API_KEY", ELEVENLABS_API_KEY), ("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)) if not v]
    if missing:
        raise RuntimeError("Missing configuration: " + ", ".join(missing))


@app.get("/")
async def index():
    return {"service": "RealEstate Hub Day 3 Voice Agent", "frontend": "voice-frontend-nextjs"}


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "google_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "google_stt_configured": bool(os.getenv("GOOGLE_API_KEY")),
        "elevenlabs_configured": bool(ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID),
        "voice_model": ELEVENLABS_MODEL,
        "stt_model": GOOGLE_STT_MODEL,
        "stt_language_codes": GOOGLE_STT_LANGUAGE_CODES or ["auto"],
    }


@app.post("/api/chat")
async def chat(payload: dict[str, Any]):
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


async def elevenlabs_stream(text: str, ws: WebSocket, turn_id: str) -> None:
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream?output_format=mp3_22050_32"
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.35,
            "similarity_boost": 0.75,
            "style": 0.25,
            "use_speaker_boost": True,
            "speed": 1.0,
        },
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0)) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            await ws.send_json({"type": "tts_start", "turn_id": turn_id})
            async for chunk in response.aiter_bytes(4096):
                if chunk:
                    await ws.send_json({"type": "tts_chunk", "turn_id": turn_id, "data": base64.b64encode(chunk).decode("ascii")})
            await ws.send_json({"type": "tts_end", "turn_id": turn_id})


async def send_filler(ws: WebSocket, turn_id: str, phrase: str) -> None:
    try:
        await elevenlabs_stream(phrase, ws, turn_id)
    except Exception as exc:
        await ws.send_json({"type": "tts_error", "turn_id": turn_id, "error": str(exc)})


async def handle_utterance(ws: WebSocket, session_id: str, text: str, state: dict[str, Any]) -> None:
    memory = SESSIONS.setdefault(session_id, ConversationMemory())
    memory.add("user", text)
    turn_id = uuid.uuid4().hex[:12]
    started = state.pop("speech_started_at", None) or time.perf_counter()
    state["response_task"] = asyncio.current_task()

    # Immediate conversational acknowledgement/filler to make the voice feel live.
    filler = "Ji bilkul..." if len(text.split()) <= 10 else "Hmm, ek second..."
    filler_task = asyncio.create_task(send_filler(ws, turn_id, filler))
    await ws.send_json({"type": "assistant_thinking", "turn_id": turn_id, "text": filler})
    try:
        answer, meta = await asyncio.to_thread(make_voice_answer, text, memory)
        await filler_task
        memory.add("assistant", answer)
        latency_ms = round((time.perf_counter() - started) * 1000)
        await ws.send_json({
            "type": "assistant_text",
            "turn_id": turn_id,
            "text": answer,
            "latency_ms": latency_ms,
            "objection": meta.get("objection"),
            "sources": meta.get("sources", []),
            "memory": memory.preferences,
        })
        await elevenlabs_stream(answer, ws, turn_id)
        await ws.send_json({"type": "turn_complete", "turn_id": turn_id, "latency_ms": latency_ms})
    except asyncio.CancelledError:
        if "filler_task" in locals() and not filler_task.done():
            filler_task.cancel()
        await ws.send_json({"type": "turn_cancelled", "turn_id": turn_id})
        raise
    except Exception as exc:
        await ws.send_json({"type": "assistant_error", "turn_id": turn_id, "error": str(exc)})
    finally:
        if state.get("response_task") is asyncio.current_task():
            state["response_task"] = None


async def google_transcript_reader(ws: WebSocket, google_ws, session_id: str, state: dict[str, Any]) -> None:
    try:
        async for raw in google_ws:
            if isinstance(raw, bytes):
                continue
            try:
                response = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if response.get("error"):
                await ws.send_json({"type": "server_error", "error": f"Google Live API: {response['error']}"})
                continue
            server_content = response.get("serverContent") or response.get("server_content")
            if not server_content:
                continue

        # Transcription-only sessions return inputTranscription events. Any model
        # content is ignored because answer generation is handled by this server.
            if server_content.get("modelTurn") or server_content.get("model_turn"):
                continue

            interim = server_content.get("interimInputTranscription") or server_content.get("interim_input_transcription")
            if interim and interim.get("text"):
                await ws.send_json({
                    "type": "transcript", "text": interim["text"],
                    "final": False, "speech_final": False,
                })

            final = server_content.get("inputTranscription") or server_content.get("input_transcription")
            if final and final.get("text"):
                text = final["text"].strip()
                if not text:
                    continue
                await ws.send_json({
                    "type": "transcript", "text": text,
                    "final": True, "speech_final": True,
                })
                if state.get("response_task") and not state["response_task"].done():
                    state["response_task"].cancel()
                task = asyncio.create_task(handle_utterance(ws, session_id, text, state))
                state["response_task"] = task
    except ConnectionClosed as exc:
        await ws.send_json({"type": "server_error", "error": f"Google Live socket closed ({exc.code}): {exc.reason or 'no reason provided'}"})


@app.websocket("/ws/voice")
async def voice_socket(ws: WebSocket):
    await ws.accept()
    session_id = uuid.uuid4().hex
    SESSIONS.setdefault(session_id, ConversationMemory())
    state: dict[str, Any] = {"response_task": None, "started": time.perf_counter()}
    google_ws = None
    reader_task = None
    try:
        await ws.send_json({"type": "session", "session_id": session_id})
        google_key = os.getenv("GOOGLE_API_KEY", "")
        if not google_key:
            await ws.send_json({"type": "config_error", "error": "GOOGLE_API_KEY is missing."})
            return

        google_url = (
            "wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
            f"?key={google_key}"
        )
        google_ws = await websockets.connect(
            google_url,
            ping_interval=20,
            ping_timeout=20,
            max_size=4 * 1024 * 1024,
        )
        setup = {
            "setup": {
                "model": f"models/{GOOGLE_STT_MODEL}",
                "generationConfig": {"responseModalities": ["TEXT"]},
                "realtimeInputConfig": {
                    "automaticActivityDetection": {
                        "disabled": False,
                        "silenceDurationMs": 700,
                    },
                },
                "systemInstruction": {
                    "parts": [{"text": "Do not speak. You are used only for speech transcription; never generate a spoken or text reply."}]
                },
                "inputAudioTranscription": {
                    "mode": "VERBATIM",
                    "customVocabulary": [
                        "DHA", "Bahria Town", "Gulberg", "Johar Town", "Model Town",
                        "Islamabad", "Lahore", "Rawalpindi", "PKR", "crore", "lakh",
                        "RealEstate Hub",
                    ],
                },
            }
        }
        if GOOGLE_STT_LANGUAGE_CODES:
            setup["setup"]["inputAudioTranscription"]["languageCodes"] = GOOGLE_STT_LANGUAGE_CODES
        await google_ws.send(json.dumps(setup))
        setup_response = json.loads(await google_ws.recv())
        if setup_response.get("error"):
            raise RuntimeError(str(setup_response["error"]))

        reader_task = asyncio.create_task(
            google_transcript_reader(ws, google_ws, session_id, state)
        )
        await ws.send_json({"type": "ready"})

        while True:
            message = await ws.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("bytes") is not None:
                audio = message["bytes"]
                if audio:
                    await google_ws.send(json.dumps({
                        "realtimeInput": {
                            "audio": {
                                "data": base64.b64encode(audio).decode("ascii"),
                                "mimeType": "audio/pcm;rate=16000",
                            }
                        }
                    }))
                continue

            if message.get("text"):
                try:
                    event = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                if event.get("type") == "speech_start":
                    state["speech_started_at"] = time.perf_counter()
                    task = state.get("response_task")
                    if task and not task.done():
                        task.cancel()
                    if task and not task.done():
                        await ws.send_json({"type": "interrupted"})

                elif event.get("type") == "speech_end":
                    try:
                        await google_ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                    except Exception:
                        pass

                elif event.get("type") == "end":
                    try:
                        await google_ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))
                    except Exception:
                        pass

                elif event.get("type") == "reset":
                    SESSIONS[session_id] = ConversationMemory()
                    await ws.send_json({"type": "memory_reset"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await ws.send_json({"type": "server_error", "error": str(exc)})
        except Exception:
            pass
    finally:
        task = state.get("response_task")
        if task and not task.done():
            task.cancel()
        if reader_task:
            reader_task.cancel()
        if google_ws:
            try:
                await google_ws.close()
            except Exception:
                pass


@app.post("/api/recording")
async def upload_recording(file: UploadFile = File(...)):
    ext = Path(file.filename or "conversation.webm").suffix or ".webm"
    name = f"{uuid.uuid4().hex}{ext}"
    path = REC_DIR / name
    path.write_bytes(await file.read())
    return {"ok": True, "file": name}


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