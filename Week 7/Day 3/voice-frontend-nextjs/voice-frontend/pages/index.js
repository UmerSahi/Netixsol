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
  const [errorBanner, setErrorBanner] = useState(null);
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
  const audioElRef = useRef(null);
  const mediaSourceRef = useRef(null);
  const sourceBufferRef = useRef(null);
  const audioQueueRef = useRef([]);
  const ttsChunksRef = useRef([]);
  const conversationRef = useRef(null);

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

  const resetAudio = useCallback(() => {
    if (audioElRef.current) {
      audioElRef.current.pause();
      audioElRef.current.src = "";
    }
    audioQueueRef.current = [];
    ttsChunksRef.current = [];
    mediaSourceRef.current = null;
    sourceBufferRef.current = null;
  }, []);

  const flushAudio = useCallback(() => {
    return;
  }, []);

  const initAudio = useCallback(() => {
    resetAudio();
    const audioEl = new Audio();
    audioEl.autoplay = true;
    audioEl.addEventListener("error", () => {
      // Clearing src during cleanup can emit an error without a media error.
      if (!audioEl.src || !audioEl.error) return;
      setErrorBanner("Audio playback failed. Check the ElevenLabs response and browser audio permissions.");
    });
    audioElRef.current = audioEl;
  }, [resetAudio, setErrorBanner]);

  const queueAudio = useCallback(
    (b64) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
      ttsChunksRef.current.push(bytes);
      audioQueueRef.current.push(bytes);
      flushAudio();
    },
    [flushAudio]
  );

  const finishAudio = useCallback(() => {
    if (!audioElRef.current || !ttsChunksRef.current.length) return;
    const blob = new Blob(ttsChunksRef.current, { type: "audio/mpeg" });
    const audioEl = audioElRef.current;
    audioEl.src = URL.createObjectURL(blob);
    audioEl.play().catch(() => {
      setErrorBanner("Browser blocked voice playback. Click Start Conversation and allow audio playback.");
    });
  }, [setErrorBanner]);

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
      setMicLevel(Math.min(100, Math.round(level * 900)));
      // Ignore low-level room/microphone noise so Google receives a real end-of-speech boundary.
      const active = level > 0.025;
      if (active && !speakingRef.current) {
        speakingRef.current = true;
        ws.send(JSON.stringify({ type: "speech_start" }));
        setStatus("Listening — speech detected", true);
      } else if (!active && speakingRef.current) {
        clearTimeout(silenceTimerRef.current);
        silenceTimerRef.current = setTimeout(() => {
          if (ws.readyState === 1 && speakingRef.current) {
            speakingRef.current = false;
            ws.send(JSON.stringify({ type: "speech_end" }));
          }
        }, 450);
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
  }, [setStatus]);

  const handle = useCallback(
    (m) => {
      if (m.type === "session") sessionIdRef.current = m.session_id;
      if (m.type === "ready") setStatus("Listening — speak now", true);
      if (m.type === "config_error") {
        setErrorBanner(`config_error: ${m.error}`);
        stopRef.current?.();
      }
      if (m.type === "transcript") {
        if (m.final) {
          if (m.text !== lastInterimRef.current) addMsg("user", m.text, "Google STT");
          lastInterimRef.current = "";
        } else {
          lastInterimRef.current = m.text;
          setStatus("Listening — " + m.text, true);
        }
      }
      if (m.type === "assistant_thinking") setStatus("Assistant thinking…", true);
      if (m.type === "assistant_text") {
        addMsg("assistant", m.text, `${m.objection || "normal"} • ${m.latency_ms} ms`);
        setLatencyText(`Latency: ${m.latency_ms} ms`);
        setMeterWidth(Math.min(100, m.latency_ms / 20));
        updateMemoryState(m.memory);
        setStatus("Speaking — you can interrupt", true);
      }
      if (m.type === "tts_chunk") queueAudio(m.data);
      if (m.type === "tts_end") finishAudio();
      if (m.type === "tts_error") {
        setErrorBanner(`Text-to-speech error: ${m.error}`);
        setStatus("Text-to-speech unavailable", false);
      }
      if (m.type === "turn_complete") setStatus("Listening — speak now", true);
      if (m.type === "turn_cancelled" || m.type === "interrupted") {
        resetAudio();
        setStatus("Interrupted — listening", true);
      }
      if (m.type === "memory_reset") {
        updateMemoryState({});
        setStatus("Memory reset", true);
      }
      if (m.type === "assistant_error" || m.type === "server_error") {
        addMsg("assistant", "Sorry ji, technical issue aa gaya.", "error");
        setErrorBanner(`${m.type}: ${m.error}`);
        setStatus("Error", false);
      }
    },
    [addMsg, finishAudio, queueAudio, resetAudio, setErrorBanner, setStatus, updateMemoryState]
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
        console.warn("recording upload failed", e);
      }
    }
    try {
      wsRef.current?.send(JSON.stringify({ type: "end" }));
      wsRef.current?.close();
    } catch {
      /* noop */
    }
    resetAudio();
    setRunning(false);
    setStatus("Disconnected");
    speakingRef.current = false;
  }, [resetAudio, setStatus]);

  useEffect(() => {
    stopRef.current = stop;
  }, [stop]);

  const start = useCallback(async () => {
    setErrorBanner(null);
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
      ws.onclose = () => {
        setStatus("Disconnected");
        setRunning(false);
        if (!errorBanner) {
          setErrorBanner("Voice connection closed before a transcript was received. Check the Uvicorn terminal for the Google Live API error.");
        }
      };
      ws.onerror = () => {
        setErrorBanner(
          "WebSocket connection error — check that the FastAPI backend is running at " +
            WS_BASE +
            " and that NEXT_PUBLIC_WS_BASE in .env.local points to it."
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
      console.error(e);
      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      setStatus("Microphone permission/error", false);
      setRunning(false);
      setErrorBanner(`Microphone error: ${e.message || e}`);
    }
  }, [handle, initAudio, setStatus, setupPCM]);

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
      setErrorBanner(`Chat error: ${e.message || e}`);
    } finally {
      setChatSending(false);
    }
  }, [addMsg, chatSending, chatText, updateMemoryState]);

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
      setSaveMsg(j.ok ? `Saved: ${j.evaluation_id}` : "Could not save evaluation");
    } catch (e) {
      setSaveMsg(`Save failed: ${e.message || e}`);
    }
  }, [notes, scores]);

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

        {errorBanner && (
          <div className="error-banner">
            <strong>Something went wrong</strong>
            {errorBanner}
          </div>
        )}
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
