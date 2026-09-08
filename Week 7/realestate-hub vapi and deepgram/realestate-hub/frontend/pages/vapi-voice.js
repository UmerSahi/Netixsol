import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import Vapi from "@vapi-ai/web";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
const VAPI_PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPI_PUBLIC_KEY || "";

function renderMessageText(text, role) {
  if (role !== "assistant") return String(text);
  return String(text)
    .split(/(\*\*[^*]+\*\*)/g)
    .map((part, index) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={index}>{part.slice(2, -2)}</strong>;
      }
      return <span key={index}>{part}</span>;
    });
}

// Vapi Web SDK's `vapi.on("message", ...)` events look like:
//   { type: "transcript", role: "user"|"assistant", transcriptType: "partial"|"final", transcript: "..." }
// This mirrors the Day-3 UI's message list from that stream instead of our
// own /ws/voice protocol.
export default function VapiVoice() {
  const [statusText, setStatusText] = useState("Disconnected");
  const [statusOn, setStatusOn] = useState(false);
  const [running, setRunning] = useState(false);
  const [messages, setMessages] = useState([]);
  const [memory, setMemory] = useState({});
  const [errorMsg, setErrorMsg] = useState("");
  const [chatText, setChatText] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const [scores, setScores] = useState({
    naturalness: 5,
    persuasiveness: 5,
    fluency: 5,
    latency: 5,
    conversation_flow: 5,
  });
  const [notes, setNotes] = useState("");
  const [saveMsg, setSaveMsg] = useState("");

  const vapiRef = useRef(null);
  const messagesRef = useRef([]);
  const sessionIdRef = useRef(null);
  const partialIndexRef = useRef({ user: null, assistant: null });
  const conversationRef = useRef(null);

  const setStatus = useCallback((text, on = false) => {
    setStatusText(text);
    setStatusOn(on);
  }, []);

  const pushMessage = useCallback((role, text, meta = "") => {
    messagesRef.current = [...messagesRef.current, { role, text, meta }];
    setMessages(messagesRef.current);
  }, []);

  // Live-updates the last partial bubble for a role instead of appending a
  // new one for every interim transcript chunk.
  const upsertPartial = useCallback((role, text) => {
    const idx = partialIndexRef.current[role];
    const next = [...messagesRef.current];
    if (idx !== null && next[idx] && next[idx].meta === "partial") {
      next[idx] = { role, text, meta: "partial" };
    } else {
      next.push({ role, text, meta: "partial" });
      partialIndexRef.current[role] = next.length - 1;
    }
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const finalizePartial = useCallback((role, text) => {
    const idx = partialIndexRef.current[role];
    const next = [...messagesRef.current];
    if (idx !== null && next[idx] && next[idx].meta === "partial") {
      next[idx] = { role, text, meta: "" };
    } else {
      next.push({ role, text, meta: "" });
    }
    partialIndexRef.current[role] = null;
    messagesRef.current = next;
    setMessages(next);
  }, []);

  useEffect(() => {
    conversationRef.current?.scrollTo({ top: conversationRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  const start = useCallback(async () => {
    setErrorMsg("");
    if (!VAPI_PUBLIC_KEY) {
      setErrorMsg("NEXT_PUBLIC_VAPI_PUBLIC_KEY is not set in the frontend's .env.local.");
      return;
    }
    setStatus("Fetching assistant config...", true);
    let config;
    try {
      const res = await fetch(`${API_BASE}/vapi/assistant-config`);
      config = await res.json();
      if (!res.ok) throw new Error(config.error || "Failed to load assistant config");
    } catch (e) {
      setErrorMsg(`Could not reach backend at ${API_BASE}: ${e.message}`);
      setStatus("Disconnected");
      return;
    }

    messagesRef.current = [];
    setMessages([]);
    partialIndexRef.current = { user: null, assistant: null };

    const vapi = new Vapi(VAPI_PUBLIC_KEY);
    vapiRef.current = vapi;

    vapi.on("call-start", () => {
      setRunning(true);
      setStatus("Connected — speak now", true);
    });

    vapi.on("call-end", () => {
      setRunning(false);
      setStatus("Disconnected");
    });

    vapi.on("speech-start", () => setStatus("Listening...", true));
    vapi.on("speech-end", () => setStatus("Thinking...", true));

    vapi.on("message", (message) => {
      if (message.type === "transcript") {
        const role = message.role === "assistant" ? "assistant" : "user";
        if (message.transcriptType === "final") {
          finalizePartial(role, message.transcript);
        } else {
          upsertPartial(role, message.transcript);
        }
      } else if (message.type === "conversation-update") {
        // Vapi's running conversation-history snapshot; not rendered
        // directly here since the transcript stream above already drives
        // the message list, but kept available for future use.
        sessionIdRef.current = message.call?.id || sessionIdRef.current;
      }
    });

    vapi.on("error", (e) => {
      setErrorMsg(typeof e === "string" ? e : e?.message || JSON.stringify(e));
      setStatus("Error");
    });

    try {
      await vapi.start(config);
    } catch (e) {
      setErrorMsg(`Failed to start Vapi call: ${e.message || e}`);
      setStatus("Disconnected");
    }
  }, [setStatus, upsertPartial, finalizePartial]);

  const stop = useCallback(() => {
    vapiRef.current?.stop();
    setRunning(false);
    setStatus("Disconnected");
  }, [setStatus]);

  useEffect(() => () => vapiRef.current?.stop(), []);

  const sendChat = useCallback(
    async (e) => {
      e.preventDefault();
      const text = chatText.trim();
      if (!text) return;
      setChatSending(true);
      pushMessage("user", text);
      setChatText("");
      try {
        const res = await fetch(`${API_BASE}/api/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, session_id: sessionIdRef.current }),
        });
        const data = await res.json();
        if (data.ok) {
          sessionIdRef.current = data.session_id;
          pushMessage("assistant", data.text);
          setMemory(data.memory || {});
        } else {
          setErrorMsg(data.error || "Chat request failed.");
        }
      } catch (e) {
        setErrorMsg(String(e));
      } finally {
        setChatSending(false);
      }
    },
    [chatText, pushMessage]
  );

  const saveEvaluation = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/evaluation`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionIdRef.current,
          scores,
          notes,
          transcript: messagesRef.current,
        }),
      });
      const data = await res.json();
      setSaveMsg(data.ok ? `Saved (${data.evaluation_id.slice(0, 8)})` : "Save failed.");
    } catch (e) {
      setSaveMsg(`Save failed: ${e}`);
    }
  }, [scores, notes]);

  return (
    <div className="page">
      <div className="header neu-raised">
        <h1 className="title">RealEstate Hub Voice Agent — Vapi</h1>
        <p className="subtitle">
          Day 4 — Vapi Web SDK owns the call. Google Live STT still does the listening (via a
          custom transcriber bridge) and the same RAG/memory/objection-handling agent still does
          the thinking; Vapi&apos;s own hosted voice now does the speaking instead of ElevenLabs.
        </p>

        <div className="status-row">
          <span className={`dot ${statusOn ? "on" : ""}`} />
          <span className="status-text">{statusText}</span>
        </div>

        <div className="btn-row">
          <button className="neu-btn primary" onClick={start} disabled={running}>
            Start Conversation
          </button>
          <button className="neu-btn stop" onClick={stop} disabled={!running}>
            Stop
          </button>
          <Link href="/crm" className="neu-btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            🏢 Open CRM & Calendar
          </Link>
        </div>
        {errorMsg && (
          <p style={{ color: "#c0392b", fontSize: 13, marginTop: 10 }}>{errorMsg}</p>
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
              <div key={i} className={`msg ${m.role}${m.meta === "partial" ? " partial" : ""}`}>
                <div>{renderMessageText(m.text, m.role)}</div>
                {m.meta === "partial" && <div className="meta">listening...</div>}
              </div>
            ))}
          </div>
          <form className="chat-composer" onSubmit={sendChat}>
            <input
              className="chat-input neu-inset"
              value={chatText}
              onChange={(e) => setChatText(e.target.value)}
              placeholder="Or type your real-estate question..."
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
              <span className="memory-empty">No preferences extracted yet (only populated via the text chat above — voice-call memory lives server-side per Vapi call id).</span>
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
                onChange={(e) => setScores((prev) => ({ ...prev, [key]: Number(e.target.value) }))}
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
          </div>
          {saveMsg && <p style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 10 }}>{saveMsg}</p>}
        </div>
      </div>
    </div>
  );
}
