"""Task 5: CRM Logging & Persistence Service.

Stores and manages:
- Call transcripts (full text, turns, AI summaries, sentiments)
- Client preferences (city, budget, property type, purpose, marla)
- Appointment history (scheduled, rescheduled, cancelled visits, calendar links)
- Follow-up reminders (dates, notes, status)
- Lead lifecycle tracking (New -> Qualified -> Meeting Scheduled -> Completed)
"""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, select, text

from config import DB_BACKEND, get_engine

metadata = MetaData()

crm_leads_table = Table(
    "crm_leads",
    metadata,
    Column("lead_id", String(64), primary_key=True),
    Column("client_name", String(128)),
    Column("phone", String(64)),
    Column("email", String(128)),
    Column("city", String(64)),
    Column("budget", String(64)),
    Column("property_type", String(64)),
    Column("purpose", String(32)),
    Column("stage", String(64)),  # New, Qualified, Meeting Scheduled, Won, Lost
    Column("preferences_json", Text),
    Column("created_at", String(64)),
    Column("updated_at", String(64)),
)

crm_appointments_table = Table(
    "crm_appointments",
    metadata,
    Column("appointment_id", String(64), primary_key=True),
    Column("lead_id", String(64)),
    Column("client_name", String(128)),
    Column("client_phone", String(64)),
    Column("property_id", String(64)),
    Column("property_title", String(256)),
    Column("agent_name", String(128)),
    Column("date_str", String(64)),
    Column("time_str", String(64)),
    Column("status", String(32)),  # scheduled, rescheduled, cancelled, completed
    Column("calendar_event_id", String(64)),
    Column("calendar_link", Text),
    Column("notes", Text),
    Column("created_at", String(64)),
    Column("updated_at", String(64)),
)

crm_transcripts_table = Table(
    "crm_transcripts",
    metadata,
    Column("transcript_id", String(64), primary_key=True),
    Column("call_id", String(64)),
    Column("lead_id", String(64)),
    Column("caller_phone", String(64)),
    Column("transcript_text", Text),
    Column("summary", Text),
    Column("sentiment", String(32)),
    Column("turns_json", Text),
    Column("created_at", String(64)),
)

crm_reminders_table = Table(
    "crm_reminders",
    metadata,
    Column("reminder_id", String(64), primary_key=True),
    Column("lead_id", String(64)),
    Column("appointment_id", String(64)),
    Column("client_name", String(128)),
    Column("reminder_date", String(64)),
    Column("note", Text),
    Column("status", String(32)),  # pending, sent, dismissed
    Column("created_at", String(64)),
)


def init_crm_db() -> None:
    """Ensure all CRM tables exist in the database."""
    engine = get_engine()
    metadata.create_all(engine)


# Initialize schema on module import
init_crm_db()


# ---------------------------------------------------------------------------
# Leads Management
# ---------------------------------------------------------------------------

def upsert_lead(
    client_name: str,
    phone: str,
    email: str = "",
    city: str = "",
    budget: str = "",
    property_type: str = "",
    purpose: str = "",
    stage: str = "Qualified",
    preferences: dict[str, Any] | None = None,
    lead_id: str | None = None,
) -> str:
    """Create or update a client lead record."""
    engine = get_engine()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    prefs_str = json.dumps(preferences or {}, ensure_ascii=False)

    with engine.begin() as conn:
        existing = None
        if lead_id:
            res = conn.execute(select(crm_leads_table).where(crm_leads_table.c.lead_id == lead_id))
            existing = res.mappings().first()
        elif phone and phone != "Not Provided":
            res = conn.execute(select(crm_leads_table).where(crm_leads_table.c.phone == phone))
            existing = res.mappings().first()

        if existing:
            target_id = existing["lead_id"]
            conn.execute(
                crm_leads_table.update()
                .where(crm_leads_table.c.lead_id == target_id)
                .values(
                    client_name=client_name or existing["client_name"],
                    email=email or existing["email"],
                    city=city or existing["city"],
                    budget=budget or existing["budget"],
                    property_type=property_type or existing["property_type"],
                    purpose=purpose or existing["purpose"],
                    stage=stage or existing["stage"],
                    preferences_json=prefs_str if preferences else existing["preferences_json"],
                    updated_at=now_iso,
                )
            )
            return target_id
        else:
            target_id = lead_id or f"lead_{uuid.uuid4().hex[:10]}"
            conn.execute(
                crm_leads_table.insert().values(
                    lead_id=target_id,
                    client_name=client_name or "New Client",
                    phone=phone or "Not Provided",
                    email=email or "",
                    city=city or "",
                    budget=budget or "",
                    property_type=property_type or "",
                    purpose=purpose or "For Sale",
                    stage=stage,
                    preferences_json=prefs_str,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            return target_id


def list_leads(limit: int = 50) -> list[dict[str, Any]]:
    """List most recent CRM leads."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(crm_leads_table)
            .order_by(crm_leads_table.c.updated_at.desc())
            .limit(limit)
        ).mappings().all()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["preferences"] = json.loads(d.get("preferences_json") or "{}")
        except Exception:
            d["preferences"] = {}
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Appointments Management
# ---------------------------------------------------------------------------

def record_appointment(
    lead_id: str,
    client_name: str,
    client_phone: str,
    property_id: str,
    property_title: str,
    agent_name: str,
    date_str: str,
    time_str: str,
    status: str = "scheduled",
    calendar_event_id: str = "",
    calendar_link: str = "",
    notes: str = "",
    appointment_id: str | None = None,
) -> str:
    """Record a new or updated appointment in CRM."""
    engine = get_engine()
    appt_id = appointment_id or f"appt_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with engine.begin() as conn:
        res = conn.execute(select(crm_appointments_table).where(crm_appointments_table.c.appointment_id == appt_id))
        existing = res.mappings().first()
        if existing:
            conn.execute(
                crm_appointments_table.update()
                .where(crm_appointments_table.c.appointment_id == appt_id)
                .values(
                    date_str=date_str,
                    time_str=time_str,
                    status=status,
                    calendar_event_id=calendar_event_id or existing["calendar_event_id"],
                    calendar_link=calendar_link or existing["calendar_link"],
                    notes=notes or existing["notes"],
                    updated_at=now_iso,
                )
            )
        else:
            conn.execute(
                crm_appointments_table.insert().values(
                    appointment_id=appt_id,
                    lead_id=lead_id,
                    client_name=client_name,
                    client_phone=client_phone,
                    property_id=property_id,
                    property_title=property_title,
                    agent_name=agent_name,
                    date_str=date_str,
                    time_str=time_str,
                    status=status,
                    calendar_event_id=calendar_event_id,
                    calendar_link=calendar_link,
                    notes=notes,
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            # Advance lead stage
            conn.execute(
                crm_leads_table.update()
                .where(crm_leads_table.c.lead_id == lead_id)
                .values(stage="Meeting Scheduled", updated_at=now_iso)
            )
    return appt_id


def list_appointments(limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    """List appointments with optional status filter."""
    engine = get_engine()
    stmt = select(crm_appointments_table)
    if status:
        stmt = stmt.where(crm_appointments_table.c.status == status)
    stmt = stmt.order_by(crm_appointments_table.c.updated_at.desc()).limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


def get_appointment(appointment_id: str) -> dict[str, Any] | None:
    """Retrieve appointment details by ID."""
    engine = get_engine()
    with engine.connect() as conn:
        res = conn.execute(select(crm_appointments_table).where(crm_appointments_table.c.appointment_id == appointment_id))
        row = res.mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Call Transcripts & Summaries
# ---------------------------------------------------------------------------

def log_call_transcript(
    call_id: str,
    lead_id: str = "",
    caller_phone: str = "",
    transcript_text: str = "",
    summary: str = "",
    sentiment: str = "Positive",
    turns: list[dict[str, Any]] | None = None,
) -> str:
    """Store complete call transcript, AI summary, and turns."""
    engine = get_engine()
    transcript_id = f"tr_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    turns_str = json.dumps(turns or [], ensure_ascii=False)

    with engine.begin() as conn:
        conn.execute(
            crm_transcripts_table.insert().values(
                transcript_id=transcript_id,
                call_id=call_id or f"call_{uuid.uuid4().hex[:8]}",
                lead_id=lead_id or "",
                caller_phone=caller_phone or "Anonymous Caller",
                transcript_text=transcript_text,
                summary=summary or "Call completed via Voice Agent",
                sentiment=sentiment or "Positive",
                turns_json=turns_str,
                created_at=now_iso,
            )
        )
    return transcript_id


def list_transcripts(limit: int = 50) -> list[dict[str, Any]]:
    """List call transcripts."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            select(crm_transcripts_table)
            .order_by(crm_transcripts_table.c.created_at.desc())
            .limit(limit)
        ).mappings().all()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["turns"] = json.loads(d.get("turns_json") or "[]")
        except Exception:
            d["turns"] = []
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Follow-up Reminders
# ---------------------------------------------------------------------------

def create_reminder(
    lead_id: str,
    client_name: str,
    reminder_date: str,
    note: str,
    appointment_id: str = "",
) -> str:
    """Create a follow-up reminder for an agent."""
    engine = get_engine()
    reminder_id = f"rem_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with engine.begin() as conn:
        conn.execute(
            crm_reminders_table.insert().values(
                reminder_id=reminder_id,
                lead_id=lead_id,
                appointment_id=appointment_id,
                client_name=client_name,
                reminder_date=reminder_date,
                note=note,
                status="pending",
                created_at=now_iso,
            )
        )
    return reminder_id


def dismiss_reminder(reminder_id: str) -> bool:
    """Mark a reminder as dismissed/completed."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            crm_reminders_table.update()
            .where(crm_reminders_table.c.reminder_id == reminder_id)
            .values(status="dismissed")
        )
    return True


def list_reminders(limit: int = 50, status: str = "pending") -> list[dict[str, Any]]:
    """List follow-up reminders."""
    engine = get_engine()
    stmt = select(crm_reminders_table)
    if status:
        stmt = stmt.where(crm_reminders_table.c.status == status)
    stmt = stmt.order_by(crm_reminders_table.c.created_at.desc()).limit(limit)

    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Dashboard Stats & Metrics
# ---------------------------------------------------------------------------

def get_crm_dashboard_stats() -> dict[str, Any]:
    """Return aggregated business metrics for CRM dashboard."""
    engine = get_engine()
    with engine.connect() as conn:
        leads_count = conn.execute(text("SELECT COUNT(*) FROM crm_leads")).scalar() or 0
        appts_count = conn.execute(text("SELECT COUNT(*) FROM crm_appointments WHERE status = 'scheduled'")).scalar() or 0
        total_appts = conn.execute(text("SELECT COUNT(*) FROM crm_appointments")).scalar() or 0
        calls_count = conn.execute(text("SELECT COUNT(*) FROM crm_transcripts")).scalar() or 0
        reminders_count = conn.execute(text("SELECT COUNT(*) FROM crm_reminders WHERE status = 'pending'")).scalar() or 0

    return {
        "total_leads": leads_count,
        "active_appointments": appts_count,
        "total_appointments": total_appts,
        "total_calls": calls_count,
        "pending_reminders": reminders_count,
    }
