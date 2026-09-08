import { useCallback, useEffect, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const WS_BASE = process.env.NEXT_PUBLIC_WS_BASE || "ws://localhost:8000";

function escapeHtml(s) {
  return String(s);
}

function renderMessageText(text, role) {
  if (role !== "assistant") return String(text);
  return String(text).split(/(\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <span key={index}>{part}</span>;
  });
}

function downsampleBuffer(buffer, inputRate, outputRate) {
  if (outputRate === inputRate) return buffer;
  const ratio = inputRate / outputRate;
  const newLength = Math.round(buffer.length / ratio);
  const result = new Float32Array(newLength);
  let offset = 0;
  for (let i = 0; i < newLength; i++) {
    const next = Math.round((i + 1) * ratio);
    let sum = 0;
    let count = 0;
    for (let j = offset; j < next && j < buffer.length; j++) {
      sum += buffer[j];
      count++;
    }
    result[i] = count ? sum / count : 0;
    offset = next;
  }
  return result;
}

function floatTo16BitPCM(float32) {
  const out = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function pcmBytes(int16) {
  return new Uint8Array(int16.buffer, int16.byteOffset, int16.byteLength);
}

function rms(buffer) {
  let sum = 0;
  for (const x of buffer) sum += x * x;
  return Math.sqrt(sum / (buffer.length || 1));
}

export default function Home() {
  const [statusText, setStatusText] = useState("Disconnected");
  const [statusOn, setStatusOn] = useState(false);
  const [latencyText, setLatencyText] = useState("Latency: —");
  const [meterWidth, setMeterWidth] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [messages, setMessages] = useState([]);
  const [memory, setMemory] = useState({});
  const [running, setRunning] = useState(false);
  const [errors, setErrors] = useState([]);
  const [scores, setScores] = useState({
    naturalness: 5,
    persuasiveness: 5,
    fluency: 5,
    latency: 5,
    conversation_flow: 5,
  });
  const [notes, setNotes] = useState("");
  const [saveMsg, setSaveMsg] = useState("");
  const [chatText, setChatText] = useState("");
  const [chatSending, setChatSending] = useState(false);

  const wsRef = useRef(null);
  const sessionIdRef = useRef(null);
  const messagesRef = useRef([]);
  const audioCtxRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const micSourceRef = useRef(null);
  const processorRef = useRef(null);
  const muteGainRef = useRef(null);
  const speakingRef = useRef(false);
  const silenceTimerRef = useRef(null);
  const lastInterimRef = useRef("");
  const recorderRef = useRef(null);
  const recordingChunksRef = useRef([]);
  const conversationRef = useRef(null);

  // Audio queue & playback refs
  const audioElRef = useRef(null);
  const audioQueueRef = useRef([]);
  const isPlayingRef = useRef(false);
  const isAssistantSpeakingRef = useRef(false);
  const currentChunkBufferRef = useRef([]);
  const lastAssistantTextRef = useRef("");

  const triggerError = useCallback((title, message, category = "General", hint = "") => {
    const id = Date.now().toString(36) + Math.random().toString(36).substring(2, 6);
    const newError = {
      id,
      title: title || "An Error Occurred",
      message: String(message || "Unknown error."),
      category: category || "System",
      hint: hint || "",
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    };
    setErrors((prev) => [newError, ...prev.slice(0, 4)]);
  }, []);

  const dismissError = useCallback((id) => {
    setErrors((prev) => prev.filter((e) => e.id !== id));
  }, []);

  const clearErrors = useCallback(() => {
    setErrors([]);
  }, []);

  const setStatus = useCallback((text, on = false) => {
    setStatusText(text);
    setStatusOn(on);
  }, []);

  const addMsg = useCallback((role, text, meta = "") => {
    const entry = { role, text, meta, time: new Date().toISOString() };
    messagesRef.current.push(entry);
    setMessages((prev) => [...prev, entry]);
  }, []);

  useEffect(() => {
    if (conversationRef.current) {
      conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
    }
  }, [messages]);

  const stopAllAudio = useCallback(() => {
    if (audioElRef.current) {
      audioElRef.current.pause();
      audioElRef.current.src = "";
    }
    if (typeof window !== "undefined" && window.speechSynthesis) {
      window.speechSynthesis.cancel();
    }
    audioQueueRef.current = [];
    currentChunkBufferRef.current = [];
    isPlayingRef.current = false;
    isAssistantSpeakingRef.current = false;
  }, []);

  const speakWithBrowserTTS = useCallback((text) => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const cleanText = text.replace(/\*\*/g, "").replace(/#/g, "").trim();
      if (!cleanText) return;
      const utterance = new SpeechSynthesisUtterance(cleanText);
      utterance.rate = 1.0;
      utterance.pitch = 1.0;
      utterance.onend = () => {
        isAssistantSpeakingRef.current = false;
        setStatus("Listening — speak now", true);
      };
      const voices = window.speechSynthesis.getVoices();
      const preferred = voices.find(
        (v) => v.lang.startsWith("ur") || v.lang.startsWith("hi") || v.lang.includes("PK") || v.lang.includes("IN")
      ) || voices.find((v) => v.lang.startsWith("en"));
      if (preferred) utterance.voice = preferred;
      window.speechSynthesis.speak(utterance);
    } catch (e) {
      console.warn("Browser speech synthesis fallback error:", e);
    }
  }, [setStatus]);

  const onTTSStart = useCallback(() => {
    isAssistantSpeakingRef.current = true;
    currentChunkBufferRef.current = [];
  }, []);

  const onTTSChunk = useCallback((b64) => {
    try {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      currentChunkBufferRef.current.push(bytes);
    } catch (e) {
      console.error("Failed to decode audio chunk", e);
    }
  }, []);

  const onTTSEnd = useCallback(() => {
    if (!currentChunkBufferRef.current.length) return;
    const blob = new Blob(currentChunkBufferRef.current, { type: "audio/mpeg" });
    const url = URL.createObjectURL(blob);
    currentChunkBufferRef.current = [];

    if (audioElRef.current) {
      audioElRef.current.pause();
      audioElRef.current.src = url;
      isPlayingRef.current = true;
      isAssistantSpeakingRef.current = true;
      audioElRef.current.play().catch((err) => {
        console.warn("Audio play error, falling back to speech synthesis:", err);
        if (lastAssistantTextRef.current) {
          speakWithBrowserTTS(lastAssistantTextRef.current);
        }
      });
    }
  }, [speakWithBrowserTTS]);

  const initAudio = useCallback(() => {
    stopAllAudio();
    const audioEl = new Audio();
    audioEl.addEventListener("ended", () => {
      isPlayingRef.current = false;
      setTimeout(() => {
        isAssistantSpeakingRef.current = false;
        setStatus("Listening — speak now", true);
      }, 400);
    });
    audioEl.addEventListener("error", () => {
      if (!audioEl.src) return;
      console.warn("Audio element error on src", audioEl.src);
      isPlayingRef.current = false;
      isAssistantSpeakingRef.current = false;
      setStatus("Listening — speak now", true);
    });
    audioElRef.current = audioEl;
  }, [setStatus, stopAllAudio]);

  const updateMemoryState = useCallback((mem) => {
    setMemory(mem || {});
  }, []);

  const setupPCM = useCallback((stream) => {
    mediaStreamRef.current = stream;
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    audioCtx.resume().catch(() => {});
    const micSource = audioCtx.createMediaStreamSource(stream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    const muteGain = audioCtx.createGain();
    muteGain.gain.value = 0;

    processor.onaudioprocess = (e) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== 1) return;

      const input = e.inputBuffer.getChannelData(0);
      const level = rms(input);
      setMicLevel(Math.min(100, Math.round(level * 1500)));

      // Dynamic threshold: when assistant is speaking, require deliberate user speech (0.020) to interrupt
      const isSpeaking = isAssistantSpeakingRef.current || isPlayingRef.current;
      const threshold = isSpeaking ? 0.020 : 0.008;

      const active = level > threshold;
      if (active && !speakingRef.current) {
        speakingRef.current = true;
        clearTimeout(silenceTimerRef.current);

        if (isSpeaking) {
          stopAllAudio();
          ws.send(JSON.stringify({ type: "interrupt" }));
          setStatus("Listening — you interrupted", true);
        } else {
          ws.send(JSON.stringify({ type: "speech_start" }));
          setStatus("Listening — speech detected", true);
        }
      } else if (!active && speakingRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = setTimeout(() => {
          if (ws.readyState === 1 && speakingRef.current) {
            speakingRef.current = false;
            ws.send(JSON.stringify({ type: "speech_end" }));
          }
        }, 350);
      }
      const down = downsampleBuffer(input, audioCtx.sampleRate, 16000);
      const pcm = floatTo16BitPCM(down);
      ws.send(pcmBytes(pcm));
    };

    micSource.connect(processor);
    processor.connect(muteGain);
    muteGain.connect(audioCtx.destination);

    audioCtxRef.current = audioCtx;
    micSourceRef.current = micSource;
    processorRef.current = processor;
    muteGainRef.current = muteGain;
  }, [setStatus, stopAllAudio]);

  const handle = useCallback(
    (m) => {
      if (m.type === "session") {
        sessionIdRef.current = m.session_id;
      }
      if (m.type === "ready") {
        setStatus("Listening — speak now", true);
      }
      if (m.type === "config_error") {
        triggerError("Configuration Error", m.error, "Config", "Check your .env settings in the project root.");
        stopRef.current?.();
      }
      if (m.type === "interrupted") {
        stopAllAudio();
        setStatus("Listening — speak now", true);
      }
      if (m.type === "transcript") {
        if (m.final) {
          addMsg("user", m.text, "Google STT");
          lastInterimRef.current = "";
          setStatus("Assistant thinking…", true);
        } else {
          lastInterimRef.current = m.text;
          setStatus("Listening — " + m.text, true);
        }
      }
      if (m.type === "assistant_thinking") {
        isAssistantSpeakingRef.current = true;
        setStatus("Assistant answering…", true);
      }
      if (m.type === "assistant_text") {
        isAssistantSpeakingRef.current = true;
        lastAssistantTextRef.current = m.text;
        addMsg("assistant", m.text, `${m.objection || "normal"} • ${m.latency_ms} ms`);
        setLatencyText(`Latency: ${m.latency_ms} ms`);
        setMeterWidth(Math.min(100, m.latency_ms / 20));
        updateMemoryState(m.memory);
        setStatus("Assistant speaking…", true);
      }
      if (m.type === "tts_start") {
        isAssistantSpeakingRef.current = true;
        onTTSStart();
      }
      if (m.type === "tts_chunk") {
        onTTSChunk(m.data);
      }
      if (m.type === "tts_end") {
        onTTSEnd();
      }
      if (m.type === "tts_error") {
        triggerError(
          "Voice Synthesis Notice",
          m.error,
          "Text-to-Speech",
          "ElevenLabs stream encountered an issue. Using browser speech synthesis fallback."
        );
        if (lastAssistantTextRef.current) {
          speakWithBrowserTTS(lastAssistantTextRef.current);
        }
      }
      if (m.type === "turn_complete") {
        setStatus("Listening — speak now", true);
      }
      if (m.type === "turn_cancelled" || m.type === "interrupted") {
        stopAllAudio();
        setStatus("Interrupted — listening", true);
      }
      if (m.type === "memory_reset") {
        updateMemoryState({});
        setStatus("Memory reset", true);
      }
      if (m.type === "assistant_error" || m.type === "server_error") {
        addMsg("assistant", "Sorry ji, technical issue aa gaya.", "error");
        triggerError(
          m.type === "server_error" ? "Server / Speech API Error" : "Assistant Processing Error",
          m.error || "An error occurred while processing the request.",
          "Backend",
          "Check terminal logs and API key configuration."
        );
        setStatus("Error", false);
      }
    },
    [addMsg, onTTSChunk, onTTSEnd, onTTSStart, setStatus, speakWithBrowserTTS, stopAllAudio, triggerError, updateMemoryState]
  );

  const stopRef = useRef(null);

  const stop = useCallback(async () => {
    try {
      clearTimeout(silenceTimerRef.current);
      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
        await new Promise((r) => setTimeout(r, 200));
      }
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      processorRef.current?.disconnect();
      micSourceRef.current?.disconnect();
      muteGainRef.current?.disconnect();
      await audioCtxRef.current?.close();
    } catch {
      /* noop */
    }
    if (recordingChunksRef.current.length) {
      try {
        const blob = new Blob(recordingChunksRef.current, { type: "audio/webm" });
        const fd = new FormData();
        fd.append("file", blob, `voice-${sessionIdRef.current || Date.now()}.webm`);
        await fetch(`${API_BASE}/api/recording`, { method: "POST", body: fd });
      } catch (e) {
        console.warn("Recording upload failed", e);
      }
    }
    try {
      wsRef.current?.send(JSON.stringify({ type: "end" }));
      wsRef.current?.close();
    } catch {
      /* noop */
    }
    stopAllAudio();
    setRunning(false);
    setStatus("Disconnected");
    speakingRef.current = false;
  }, [setStatus, stopAllAudio]);

  useEffect(() => {
    stopRef.current = stop;
  }, [stop]);

  const start = useCallback(async () => {
    setRunning(true);
    messagesRef.current = [];
    setMessages([]);
    initAudio();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      recordingChunksRef.current = [];
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      recorderRef.current = recorder;

      const ws = new WebSocket(`${WS_BASE}/ws/voice`);
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onopen = () => setStatus("Connecting Google speech service...", true);
      ws.onclose = (event) => {
        setStatus("Disconnected");
        setRunning(false);
        if (!event.wasClean) {
          triggerError(
            "Voice Connection Closed",
            `WebSocket disconnected (code ${event.code}).`,
            "Connection",
            "Ensure the FastAPI backend is running on " + WS_BASE
          );
        }
      };
      ws.onerror = () => {
        triggerError(
          "WebSocket Connection Failed",
          "Could not connect to FastAPI backend at " + WS_BASE,
          "Connection",
          "Make sure `python -m uvicorn day3_voice_server:app --port 8000` is running."
        );
        setStatus("Connection error");
      };

      recorder.ondataavailable = (e) => {
        if (e.data.size) recordingChunksRef.current.push(e.data);
      };

      ws.onmessage = (e) => {
        if (typeof e.data !== "string") return;
        try {
          const message = JSON.parse(e.data);
          if (message.type === "ready") {
            setupPCM(stream);
            recorder.start(250);
          }
          handle(message);
        } catch (err) {
          console.error("Failed to parse WS message", err, e.data);
        }
      };
    } catch (e) {
      console.error("Mic start error:", e);
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      setStatus("Microphone permission/error", false);
      setRunning(false);
      triggerError(
        "Microphone Access Blocked",
        e.message || "Microphone permission was denied or device not found.",
        "Microphone",
        "Click the camera/mic icon in the browser address bar and choose 'Allow'."
      );
    }
  }, [handle, initAudio, setStatus, setupPCM, triggerError]);

  const resetMemory = useCallback(() => {
    wsRef.current?.send(JSON.stringify({ type: "reset" }));
    messagesRef.current = [];
    setMessages([]);
    setMemory({});
  }, []);

  const sendChat = useCallback(async (event) => {
    event.preventDefault();
    const text = chatText.trim();
    if (!text || chatSending) return;

    setChatText("");
    setChatSending(true);
    addMsg("user", text, "Typed chat");
    try {
      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionIdRef.current, text }),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "Chat request failed.");
      sessionIdRef.current = result.session_id;
      addMsg("assistant", result.text, `${result.objection || "normal"} • Typed chat`);
      updateMemoryState(result.memory);
    } catch (e) {
      addMsg("assistant", "Sorry ji, technical issue aa gaya.", "error");
      triggerError("Chat Request Failed", e.message || "Could not reach chat API.", "Chat API", "Verify that the backend is running at " + API_BASE);
    } finally {
      setChatSending(false);
    }
  }, [addMsg, chatSending, chatText, triggerError, updateMemoryState]);

  const saveEvaluation = useCallback(async () => {
    setSaveMsg("");
    const payload = {
      session_id: sessionIdRef.current,
      messages: messagesRef.current,
      scores,
      notes,
    };
    try {
      const r = await fetch(`${API_BASE}/api/evaluation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json();
      if (!r.ok || !j.ok) throw new Error(j.error || "Failed to save evaluation.");
      setSaveMsg(`Saved: ${j.evaluation_id}`);
    } catch (e) {
      const msg = `Save failed: ${e.message || e}`;
      setSaveMsg(msg);
      triggerError("Evaluation Save Error", e.message || "Failed to save evaluation record.", "Evaluation", "Check backend database status.");
    }
  }, [notes, scores, triggerError]);

  const downloadLog = useCallback(() => {
    const blob = new Blob(
      [JSON.stringify({ sessionId: sessionIdRef.current, messages: messagesRef.current }, null, 2)],
      { type: "application/json" }
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `voice-session-${sessionIdRef.current || "local"}.json`;
    a.click();
  }, []);

  useEffect(() => {
    const handler = () => stopRef.current?.();
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  return (
    <div className="page">
      {/* Floating Error Popups / Toast Notification Container */}
      {errors.length > 0 && (
        <div className="error-popup-container" role="alert" aria-live="assertive">
          <div className="error-popup-header">
            <span className="error-popup-count">
              <span className="error-indicator-dot" />
              <strong>{errors.length} Active {errors.length === 1 ? "Notification" : "Notifications"}</strong>
            </span>
            <button className="error-popup-clear-all" onClick={clearErrors} title="Dismiss all notifications">
              Dismiss all
            </button>
          </div>
          <div className="error-popup-list">
            {errors.map((err) => (
              <div key={err.id} className="error-popup-card neu-raised">
                <div className="error-popup-card-header">
                  <span className="error-category-badge">{err.category}</span>
                  <span className="error-timestamp">{err.timestamp}</span>
                  <button
                    className="error-popup-close-btn"
                    onClick={() => dismissError(err.id)}
                    aria-label="Dismiss notification"
                    title="Dismiss"
                  >
                    ✕
                  </button>
                </div>
                <h4 className="error-popup-title">{err.title}</h4>
                <p className="error-popup-msg">{err.message}</p>
                {err.hint && (
                  <div className="error-popup-hint">
                    <span className="hint-icon">💡</span>
                    <span>{err.hint}</span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="header neu-raised">
        <h1 className="title">RealEstate Hub Voice Agent</h1>
        <p className="subtitle">
          Day 3 — Google Live streaming STT → RAG/LLM → Voice, with memory, barge-in, natural
          Pakistani conversation and human evaluation.
        </p>

        <div className="status-row">
          <span className={`dot ${statusOn ? "on" : ""}`} />
          <span className="status-text">{escapeHtml(statusText)}</span>
          <span className="pill neu-flat">{latencyText}</span>
        </div>
        <div className="mic-meter" aria-label={`Microphone level ${micLevel}%`}>
          <span>Mic level</span>
          <div className="mic-track"><div style={{ width: `${micLevel}%` }} /></div>
        </div>

        <div className="btn-row">
          <button className="neu-btn primary" onClick={start} disabled={running}>
            Start Conversation
          </button>
          <button className="neu-btn stop" onClick={stop} disabled={!running}>
            Stop
          </button>
          <button className="neu-btn" onClick={resetMemory} disabled={!running}>
            Reset Memory
          </button>
        </div>
      </div>

      <div className="grid">
        <div className="panel neu-raised">
          <h2>Live conversation</h2>
          <div className="conversation neu-inset" ref={conversationRef}>
            {messages.length === 0 && (
              <div className="empty-state">No conversation yet — click Start Conversation and speak.</div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}${m.meta === "error" ? " error" : ""}`}>
                <div>{renderMessageText(m.text, m.role)}</div>
                <div className="meta">{m.meta}</div>
              </div>
            ))}
          </div>
          <form className="chat-composer" onSubmit={sendChat}>
            <input
              className="chat-input neu-inset"
              value={chatText}
              onChange={(e) => setChatText(e.target.value)}
              placeholder="Type your real-estate question..."
              aria-label="Type your question"
              disabled={chatSending}
            />
            <button className="neu-btn primary chat-send" type="submit" disabled={!chatText.trim() || chatSending}>
              {chatSending ? "Sending..." : "Send"}
            </button>
          </form>
        </div>

        <div className="panel neu-raised">
          <h2>Remembered context</h2>
          <div className="memory-box neu-inset">
            {Object.keys(memory).length === 0 ? (
              <span className="memory-empty">No preferences extracted yet.</span>
            ) : (
              Object.entries(memory).map(([k, v]) => (
                <span className="memory-pill" key={k}>
                  {k}: {String(v)}
                </span>
              ))
            )}
          </div>

          <div className="section-divider" />

          <h2>Human evaluation</h2>
          {[
            ["naturalness", "Naturalness"],
            ["persuasiveness", "Persuasiveness"],
            ["fluency", "Fluency"],
            ["latency", "Latency"],
            ["conversation_flow", "Conversation flow"],
          ].map(([key, label]) => (
            <div className="eval-row" key={key}>
              <label htmlFor={key}>{label}</label>
              <input
                id={key}
                type="number"
                min={1}
                max={10}
                value={scores[key]}
                onChange={(e) =>
                  setScores((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                }
              />
            </div>
          ))}

          <textarea
            className="neu-inset"
            placeholder="Evaluator notes..."
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />

          <div className="btn-row">
            <button className="neu-btn primary" onClick={saveEvaluation}>
              Save evaluation
            </button>
            <button className="neu-btn" onClick={downloadLog}>
              Download log
            </button>
          </div>
          {saveMsg && <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10 }}>{saveMsg}</p>}
        </div>
      </div>
    </div>
  );
}
