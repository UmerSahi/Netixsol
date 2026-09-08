import { useCallback, useEffect, useState } from "react";
import Head from "next/head";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export default function CRMDashboard() {
  const [activeTab, setActiveTab] = useState("appointments");
  const [stats, setStats] = useState({
    total_leads: 0,
    active_appointments: 0,
    total_appointments: 0,
    total_calls: 0,
    pending_reminders: 0,
  });
  const [appointments, setAppointments] = useState([]);
  const [leads, setLeads] = useState([]);
  const [transcripts, setTranscripts] = useState([]);
  const [reminders, setReminders] = useState([]);
  const [emails, setEmails] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedTranscript, setSelectedTranscript] = useState(null);

  // Reschedule / Cancel modal state
  const [modalType, setModalType] = useState(null); // "reschedule", "cancel", "book"
  const [targetAppt, setTargetAppt] = useState(null);
  const [reschedDate, setReschedDate] = useState("");
  const [reschedTime, setReschedTime] = useState("");
  const [cancelReason, setCancelReason] = useState("");

  // New Booking form state
  const [newClientName, setNewClientName] = useState("");
  const [newClientPhone, setNewClientPhone] = useState("");
  const [newAgent, setNewAgent] = useState("Ahmed Raza");
  const [newPropTitle, setNewPropTitle] = useState("5 Marla House in DHA Defence, Lahore");
  const [newPropId, setNewPropId] = useState("PROP-1116");
  const [newDate, setNewDate] = useState("Tomorrow");
  const [newTime, setNewTime] = useState("3:00 PM");
  const [newNotes, setNewNotes] = useState("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [sRes, aRes, lRes, tRes, rRes, eRes] = await Promise.all([
        fetch(`${API_BASE}/api/crm/dashboard`).then((r) => r.json()).catch(() => ({})),
        fetch(`${API_BASE}/api/appointments`).then((r) => r.json()).catch(() => []),
        fetch(`${API_BASE}/api/crm/leads`).then((r) => r.json()).catch(() => []),
        fetch(`${API_BASE}/api/crm/transcripts`).then((r) => r.json()).catch(() => []),
        fetch(`${API_BASE}/api/crm/reminders`).then((r) => r.json()).catch(() => []),
        fetch(`${API_BASE}/api/emails`).then((r) => r.json()).catch(() => []),
      ]);
      setStats(sRes || {});
      setAppointments(Array.isArray(aRes) ? aRes : []);
      setLeads(Array.isArray(lRes) ? lRes : []);
      setTranscripts(Array.isArray(tRes) ? tRes : []);
      setReminders(Array.isArray(rRes) ? rRes : []);
      setEmails(Array.isArray(eRes) ? eRes : []);
    } catch (err) {
      console.error("Failed fetching CRM data:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleReschedule = async () => {
    if (!targetAppt) return;
    try {
      await fetch(`${API_BASE}/api/appointments/${targetAppt.appointment_id}/reschedule`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          new_date_str: reschedDate || "Tomorrow",
          new_time_str: reschedTime || "4:00 PM",
          reason: "Rescheduled via CRM dashboard",
        }),
      });
      setModalType(null);
      fetchData();
    } catch (err) {
      alert(`Error rescheduling: ${err}`);
    }
  };

  const handleCancel = async () => {
    if (!targetAppt) return;
    try {
      await fetch(`${API_BASE}/api/appointments/${targetAppt.appointment_id}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: cancelReason || "Cancelled by client request",
        }),
      });
      setModalType(null);
      fetchData();
    } catch (err) {
      alert(`Error cancelling: ${err}`);
    }
  };

  const handleDismissReminder = async (id) => {
    try {
      await fetch(`${API_BASE}/api/crm/reminders/${id}/dismiss`, { method: "POST" });
      fetchData();
    } catch (err) {
      alert(`Error: ${err}`);
    }
  };

  const handleCreateBooking = async (e) => {
    e.preventDefault();
    try {
      await fetch(`${API_BASE}/api/appointments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_name: newClientName || "Direct Walk-in Client",
          client_phone: newClientPhone || "03001234567",
          employee_name: newAgent,
          property_title: newPropTitle,
          property_id: newPropId,
          date_str: newDate,
          time_str: newTime,
          notes: newNotes || "Created manually from CRM Dashboard",
        }),
      });
      setModalType(null);
      fetchData();
    } catch (err) {
      alert(`Error creating booking: ${err}`);
    }
  };

  return (
    <div className="page">
      <Head>
        <title>CRM & Business Automation — RealEstate Hub</title>
      </Head>

      <div className="header neu-raised" style={{ marginBottom: "24px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", width: "100%" }}>
          <div>
            <h1 style={{ margin: 0, fontSize: "28px" }}>🏢 Business & CRM Dashboard</h1>
            <p style={{ margin: "4px 0 0", opacity: 0.8, fontSize: "14px" }}>
              Week 7 — Day 4: Google Calendar, Email Automation, Appointments & Workflows
            </p>
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <Link href="/" className="btn neu-raised" style={{ padding: "8px 16px", textDecoration: "none", fontSize: "13px" }}>
              💬 Chat RAG
            </Link>
            <Link href="/vapi-voice" className="btn neu-raised" style={{ padding: "8px 16px", textDecoration: "none", fontSize: "13px" }}>
              🎙️ Voice Agent
            </Link>
            <button onClick={() => setModalType("book")} className="btn neu-raised" style={{ background: "var(--accent)", color: "#fff", padding: "8px 16px", fontSize: "13px", fontWeight: "bold" }}>
              ➕ Book Visit
            </button>
          </div>
        </div>
      </div>

      {/* Overview Stat Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "16px", marginBottom: "28px" }}>
        <div className="neu-raised" style={{ padding: "18px", borderRadius: "14px", textAlign: "center" }}>
          <div style={{ fontSize: "28px", fontWeight: "bold", color: "var(--accent)" }}>{stats.total_leads || leads.length}</div>
          <div style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>👥 Total CRM Leads</div>
        </div>
        <div className="neu-raised" style={{ padding: "18px", borderRadius: "14px", textAlign: "center" }}>
          <div style={{ fontSize: "28px", fontWeight: "bold", color: "var(--success)" }}>{stats.active_appointments || appointments.filter(a => a.status === 'scheduled').length}</div>
          <div style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>📅 Active Visits</div>
        </div>
        <div className="neu-raised" style={{ padding: "18px", borderRadius: "14px", textAlign: "center" }}>
          <div style={{ fontSize: "28px", fontWeight: "bold", color: "var(--text)" }}>{stats.total_calls || transcripts.length}</div>
          <div style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>🎙️ Voice Calls Logged</div>
        </div>
        <div className="neu-raised" style={{ padding: "18px", borderRadius: "14px", textAlign: "center" }}>
          <div style={{ fontSize: "28px", fontWeight: "bold", color: "var(--danger)" }}>{stats.pending_reminders || reminders.length}</div>
          <div style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>🔔 Reminders Due</div>
        </div>
      </div>

      {/* Tabs Navigation */}
      <div style={{ display: "flex", gap: "10px", marginBottom: "20px", borderBottom: "2px solid rgba(0,0,0,0.06)", paddingBottom: "10px" }}>
        {[
          { id: "appointments", label: `📅 Appointments (${appointments.length})` },
          { id: "leads", label: `👥 Leads & Prefs (${leads.length})` },
          { id: "transcripts", label: `🎙️ Call Transcripts (${transcripts.length})` },
          { id: "reminders", label: `🔔 Follow-ups (${reminders.length})` },
          { id: "emails", label: `✉️ Email Audit (${emails.length})` },
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`btn ${activeTab === tab.id ? "neu-inset" : "neu-raised"}`}
            style={{
              padding: "10px 18px",
              fontWeight: activeTab === tab.id ? "bold" : "normal",
              color: activeTab === tab.id ? "var(--accent)" : "inherit",
              borderRadius: "10px",
              fontSize: "14px",
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* TAB 1: Appointments */}
      {activeTab === "appointments" && (
        <div className="neu-raised" style={{ padding: "20px", borderRadius: "16px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
            <h2 style={{ margin: 0, fontSize: "18px" }}>Scheduled Property Visits</h2>
            <button onClick={fetchData} className="btn neu-raised" style={{ padding: "6px 12px", fontSize: "12px" }}>
              🔄 Refresh
            </button>
          </div>

          {appointments.length === 0 ? (
            <p style={{ color: "var(--text-muted)", textAlign: "center", padding: "30px 0" }}>
              No appointments recorded yet. Say <em>"Visit schedule karni hai"</em> during a voice call or click <strong>Book Visit</strong> above!
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {appointments.map((appt) => (
                <div
                  key={appt.appointment_id}
                  className="neu-inset"
                  style={{ padding: "16px", borderRadius: "12px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "12px" }}
                >
                  <div style={{ flex: "1 1 300px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <strong style={{ fontSize: "16px" }}>{appt.property_title || appt.property_id}</strong>
                      <span
                        style={{
                          padding: "2px 8px",
                          borderRadius: "12px",
                          fontSize: "11px",
                          fontWeight: "bold",
                          textTransform: "uppercase",
                          background: appt.status === "scheduled" ? "rgba(39,142,115,0.2)" : appt.status === "rescheduled" ? "rgba(217,119,6,0.2)" : "rgba(198,83,105,0.2)",
                          color: appt.status === "scheduled" ? "var(--success)" : appt.status === "rescheduled" ? "#d97706" : "var(--danger)",
                        }}
                      >
                        {appt.status}
                      </span>
                    </div>
                    <div style={{ fontSize: "13px", color: "var(--text-muted)", marginTop: "4px" }}>
                      👤 Client: <strong>{appt.client_name}</strong> ({appt.client_phone}) &bull; 👔 Agent: <strong>{appt.agent_name}</strong>
                    </div>
                    <div style={{ fontSize: "13px", marginTop: "4px" }}>
                      📅 <strong>{appt.date_str}</strong> at <strong>{appt.time_str}</strong> {appt.notes && `— ${appt.notes}`}
                    </div>
                  </div>

                  <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                    {appt.calendar_link && (
                      <a
                        href={appt.calendar_link}
                        target="_blank"
                        rel="noreferrer"
                        className="btn neu-raised"
                        style={{ padding: "6px 12px", fontSize: "12px", textDecoration: "none", color: "#0284c7" }}
                      >
                        📅 Google Cal
                      </a>
                    )}
                    {appt.status !== "cancelled" && (
                      <>
                        <button
                          onClick={() => {
                            setTargetAppt(appt);
                            setReschedDate(appt.date_str);
                            setReschedTime(appt.time_str);
                            setModalType("reschedule");
                          }}
                          className="btn neu-raised"
                          style={{ padding: "6px 12px", fontSize: "12px" }}
                        >
                          🗓️ Reschedule
                        </button>
                        <button
                          onClick={() => {
                            setTargetAppt(appt);
                            setModalType("cancel");
                          }}
                          className="btn neu-raised"
                          style={{ padding: "6px 12px", fontSize: "12px", color: "var(--danger)" }}
                        >
                          ❌ Cancel
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 2: Leads */}
      {activeTab === "leads" && (
        <div className="neu-raised" style={{ padding: "20px", borderRadius: "16px" }}>
          <h2 style={{ margin: "0 0 16px 0", fontSize: "18px" }}>CRM Leads & Customer Preferences</h2>
          {leads.length === 0 ? (
            <p style={{ color: "var(--text-muted)", textAlign: "center", padding: "30px 0" }}>No leads recorded yet.</p>
          ) : (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: "14px" }}>
              {leads.map((lead) => (
                <div key={lead.lead_id} className="neu-inset" style={{ padding: "16px", borderRadius: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong style={{ fontSize: "15px" }}>{lead.client_name}</strong>
                    <span style={{ fontSize: "11px", padding: "2px 8px", borderRadius: "10px", background: "var(--accent-soft)", color: "var(--accent)", fontWeight: "bold" }}>
                      {lead.stage}
                    </span>
                  </div>
                  <div style={{ fontSize: "13px", marginTop: "6px" }}>📞 {lead.phone}</div>
                  <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "4px" }}>
                    City: {lead.city || "Unspecified"} &bull; Type: {lead.property_type || "Any"} &bull; Purpose: {lead.purpose}
                  </div>
                  {lead.budget && <div style={{ fontSize: "12px", marginTop: "2px" }}>💰 Budget: {lead.budget}</div>}
                  <div style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "8px", borderTop: "1px dashed #ccc", paddingTop: "6px" }}>
                    ID: {lead.lead_id} &bull; Updated: {new Date(lead.updated_at).toLocaleString()}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 3: Transcripts */}
      {activeTab === "transcripts" && (
        <div className="neu-raised" style={{ padding: "20px", borderRadius: "16px" }}>
          <h2 style={{ margin: "0 0 16px 0", fontSize: "18px" }}>Voice Call Transcripts & Summaries</h2>
          {transcripts.length === 0 ? (
            <p style={{ color: "var(--text-muted)", textAlign: "center", padding: "30px 0" }}>No voice transcripts logged yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {transcripts.map((tr) => (
                <div key={tr.transcript_id} className="neu-inset" style={{ padding: "16px", borderRadius: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                    <strong>📞 Caller: {tr.caller_phone}</strong>
                    <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>{new Date(tr.created_at).toLocaleString()}</span>
                  </div>
                  <div style={{ fontSize: "14px", fontWeight: "500", color: "var(--accent)" }}>💡 Summary: {tr.summary}</div>
                  {tr.transcript_text && (
                    <div style={{ fontSize: "13px", marginTop: "8px", background: "rgba(255,255,255,0.6)", padding: "10px", borderRadius: "8px", whiteSpace: "pre-wrap" }}>
                      {tr.transcript_text}
                    </div>
                  )}
                  {tr.turns && tr.turns.length > 0 && (
                    <div style={{ marginTop: "10px" }}>
                      <button
                        onClick={() => setSelectedTranscript(selectedTranscript === tr.transcript_id ? null : tr.transcript_id)}
                        className="btn neu-raised"
                        style={{ padding: "4px 10px", fontSize: "11px" }}
                      >
                        {selectedTranscript === tr.transcript_id ? "Hide Turns" : `View ${tr.turns.length} Turns`}
                      </button>
                      {selectedTranscript === tr.transcript_id && (
                        <div style={{ marginTop: "8px", display: "flex", flexDirection: "column", gap: "6px" }}>
                          {tr.turns.map((t, idx) => (
                            <div key={idx} style={{ fontSize: "12px", padding: "4px 8px", borderRadius: "6px", background: t.role === "user" ? "rgba(57,159,206,0.1)" : "rgba(39,142,115,0.1)" }}>
                              <strong>{t.role === "user" ? "👤 Caller" : "🤖 Agent"}:</strong> {t.text || t.content}
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 4: Reminders */}
      {activeTab === "reminders" && (
        <div className="neu-raised" style={{ padding: "20px", borderRadius: "16px" }}>
          <h2 style={{ margin: "0 0 16px 0", fontSize: "18px" }}>Follow-up Reminders for Agents</h2>
          {reminders.length === 0 ? (
            <p style={{ color: "var(--text-muted)", textAlign: "center", padding: "30px 0" }}>No pending reminders. All follow-ups are clear!</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              {reminders.map((rem) => (
                <div key={rem.reminder_id} className="neu-inset" style={{ padding: "14px 18px", borderRadius: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <div style={{ fontSize: "14px", fontWeight: "bold" }}>🔔 Due: {rem.reminder_date}</div>
                    <div style={{ fontSize: "13px", marginTop: "4px" }}>{rem.note}</div>
                    <div style={{ fontSize: "12px", color: "var(--text-muted)", marginTop: "2px" }}>Client: {rem.client_name}</div>
                  </div>
                  <button
                    onClick={() => handleDismissReminder(rem.reminder_id)}
                    className="btn neu-raised"
                    style={{ padding: "6px 14px", fontSize: "12px", color: "var(--success)" }}
                  >
                    ✓ Complete / Dismiss
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* TAB 5: Emails */}
      {activeTab === "emails" && (
        <div className="neu-raised" style={{ padding: "20px", borderRadius: "16px" }}>
          <h2 style={{ margin: "0 0 16px 0", fontSize: "18px" }}>Dispatched Email Notifications Log</h2>
          {emails.length === 0 ? (
            <p style={{ color: "var(--text-muted)", textAlign: "center", padding: "30px 0" }}>No automated emails recorded in audit store yet.</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
              {emails.map((em) => (
                <div key={em.email_id} className="neu-inset" style={{ padding: "16px", borderRadius: "12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <strong style={{ fontSize: "15px" }}>{em.subject}</strong>
                    <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>{new Date(em.sent_at).toLocaleString()}</span>
                  </div>
                  <div style={{ fontSize: "13px", marginTop: "4px", color: "var(--text-muted)" }}>
                    Recipient: <strong>{em.recipient}</strong> &bull; Employee: <strong>{em.employee_name}</strong> &bull; Client: <strong>{em.client_name}</strong>
                  </div>
                  <div style={{ fontSize: "12px", marginTop: "8px", background: "rgba(255,255,255,0.7)", padding: "10px", borderRadius: "8px", whiteSpace: "pre-wrap", fontFamily: "monospace" }}>
                    {em.plain_text}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* MODAL: Reschedule */}
      {modalType === "reschedule" && targetAppt && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div className="neu-raised" style={{ background: "white", padding: "24px", borderRadius: "16px", width: "90%", maxWidth: "420px" }}>
            <h3 style={{ margin: "0 0 12px 0" }}>🗓️ Reschedule Appointment</h3>
            <p style={{ fontSize: "13px", color: "var(--text-muted)", margin: "0 0 16px 0" }}>
              Reschedule visit for <strong>{targetAppt.property_title}</strong> with {targetAppt.client_name}.
            </p>
            <div style={{ marginBottom: "12px" }}>
              <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>New Date</label>
              <input
                type="text"
                value={reschedDate}
                onChange={(e) => setReschedDate(e.target.value)}
                placeholder="e.g. Tomorrow, Friday, 2026-09-02"
                style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
              />
            </div>
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>New Time</label>
              <input
                type="text"
                value={reschedTime}
                onChange={(e) => setReschedTime(e.target.value)}
                placeholder="e.g. 5:00 PM, 11:00 AM"
                style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button onClick={() => setModalType(null)} className="btn neu-raised" style={{ padding: "8px 16px" }}>
                Cancel
              </button>
              <button onClick={handleReschedule} className="btn neu-raised" style={{ background: "var(--accent)", color: "#fff", padding: "8px 16px" }}>
                Confirm Reschedule
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Cancel */}
      {modalType === "cancel" && targetAppt && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div className="neu-raised" style={{ background: "white", padding: "24px", borderRadius: "16px", width: "90%", maxWidth: "420px" }}>
            <h3 style={{ margin: "0 0 12px 0", color: "var(--danger)" }}>❌ Cancel Appointment</h3>
            <p style={{ fontSize: "13px", color: "var(--text-muted)", margin: "0 0 16px 0" }}>
              Are you sure you want to cancel the visit for <strong>{targetAppt.property_title}</strong> with {targetAppt.client_name}?
            </p>
            <div style={{ marginBottom: "20px" }}>
              <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Reason for cancellation</label>
              <input
                type="text"
                value={cancelReason}
                onChange={(e) => setCancelReason(e.target.value)}
                placeholder="e.g. Client requested cancellation"
                style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
              />
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button onClick={() => setModalType(null)} className="btn neu-raised" style={{ padding: "8px 16px" }}>
                Keep Appointment
              </button>
              <button onClick={handleCancel} className="btn neu-raised" style={{ background: "var(--danger)", color: "#fff", padding: "8px 16px" }}>
                Confirm Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Book Visit */}
      {modalType === "book" && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}>
          <div className="neu-raised" style={{ background: "white", padding: "24px", borderRadius: "16px", width: "90%", maxWidth: "480px" }}>
            <h3 style={{ margin: "0 0 16px 0" }}>➕ Book Property Visit</h3>
            <form onSubmit={handleCreateBooking}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "10px" }}>
                <div>
                  <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Client Name</label>
                  <input
                    type="text"
                    value={newClientName}
                    onChange={(e) => setNewClientName(e.target.value)}
                    placeholder="e.g. Ali Khan"
                    required
                    style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Phone Number</label>
                  <input
                    type="text"
                    value={newClientPhone}
                    onChange={(e) => setNewClientPhone(e.target.value)}
                    placeholder="03001234567"
                    required
                    style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                  />
                </div>
              </div>

              <div style={{ marginBottom: "10px" }}>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Property Title</label>
                <input
                  type="text"
                  value={newPropTitle}
                  onChange={(e) => setNewPropTitle(e.target.value)}
                  style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "10px" }}>
                <div>
                  <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Assigned Agent</label>
                  <select
                    value={newAgent}
                    onChange={(e) => setNewAgent(e.target.value)}
                    style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                  >
                    <option value="Ahmed Raza">Ahmed Raza (Lahore)</option>
                    <option value="Sana Malik">Sana Malik (Lahore)</option>
                    <option value="Bilal Chaudhry">Bilal Chaudhry (Lahore)</option>
                    <option value="Ayesha Farooq">Ayesha Farooq (Islamabad)</option>
                    <option value="Usman Tariq">Usman Tariq (Islamabad)</option>
                    <option value="Hina Shaikh">Hina Shaikh (Islamabad)</option>
                    <option value="Faisal Mehmood">Faisal Mehmood (Rawalpindi)</option>
                    <option value="Mahnoor Iqbal">Mahnoor Iqbal (Rawalpindi)</option>
                  </select>
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Date & Time</label>
                  <div style={{ display: "flex", gap: "4px" }}>
                    <input
                      type="text"
                      value={newDate}
                      onChange={(e) => setNewDate(e.target.value)}
                      placeholder="Tomorrow"
                      style={{ width: "50%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                    />
                    <input
                      type="text"
                      value={newTime}
                      onChange={(e) => setNewTime(e.target.value)}
                      placeholder="3:00 PM"
                      style={{ width: "50%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                    />
                  </div>
                </div>
              </div>

              <div style={{ marginBottom: "16px" }}>
                <label style={{ display: "block", fontSize: "12px", fontWeight: "bold", marginBottom: "4px" }}>Notes / Requirements</label>
                <input
                  type="text"
                  value={newNotes}
                  onChange={(e) => setNewNotes(e.target.value)}
                  placeholder="e.g. Client wants corner plot or near park"
                  style={{ width: "100%", padding: "8px", borderRadius: "8px", border: "1px solid #ccc" }}
                />
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                <button type="button" onClick={() => setModalType(null)} className="btn neu-raised" style={{ padding: "8px 16px" }}>
                  Cancel
                </button>
                <button type="submit" className="btn neu-raised" style={{ background: "var(--accent)", color: "#fff", padding: "8px 16px" }}>
                  Book Visit
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
