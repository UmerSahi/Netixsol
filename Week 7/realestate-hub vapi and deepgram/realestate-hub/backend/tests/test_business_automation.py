"""Automated test suite for Week 7 — Day 4: Workflows, Scheduling & Business Automation.

Tests:
1. Google Calendar Integration (create, update, delete events, iCal format, web template link).
2. Email Automation (agent mapping, HTML & plaintext template rendering, audit logging).
3. Appointment Management (complete booking -> reschedule -> cancel lifecycle).
4. n8n Workflow Automation (validates JSON structure, intent classifier, retry configuration).
5. CRM Persistence (leads, appointments, transcripts, reminders, and dashboard stats).
"""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from calendar_service import create_calendar_event, update_calendar_event, delete_calendar_event, CALENDAR_STORAGE_DIR
from email_service import send_employee_appointment_email, get_agent_email, list_sent_emails
from appointment_manager import book_appointment, reschedule_appointment, cancel_appointment
from crm_service import (
    upsert_lead,
    list_leads,
    record_appointment,
    get_appointment,
    list_appointments,
    log_call_transcript,
    list_transcripts,
    create_reminder,
    dismiss_reminder,
    list_reminders,
    get_crm_dashboard_stats,
)


def test_task1_calendar_integration():
    """Task 1: Create, update, and delete calendar events with all required fields."""
    event = create_calendar_event(
        client_name="Kamran Akmal",
        phone="03009876543",
        employee="Ahmed Raza",
        property_title="10 Marla House in DHA Defence, Lahore",
        property_id="PROP-1025",
        date_str="2026-09-05",
        time_str="4:00 PM",
        notes="Client interested in corner plot near park",
    )
    assert event.event_id.startswith("evt_")
    assert event.client_name == "Kamran Akmal"
    assert event.employee == "Ahmed Raza"
    assert "calendar.google.com" in event.google_calendar_link
    assert (CALENDAR_STORAGE_DIR / f"{event.event_id}.ics").exists()

    # Test Reschedule
    updated = update_calendar_event(event.event_id, date_str="2026-09-06", time_str="5:30 PM", notes="Client delayed")
    assert updated is not None
    assert updated.date_str == "2026-09-06"
    assert updated.status == "rescheduled"

    # Test Delete/Cancel
    deleted = delete_calendar_event(event.event_id, reason="Client conflict")
    assert deleted is True


def test_task2_email_automation():
    """Task 2: Send and verify employee notification emails."""
    agent_email = get_agent_email("Sana Malik", ignore_override=True)
    assert agent_email == "sana.malik@realestatehub.pk"

    result = send_employee_appointment_email(
        employee_name="Sana Malik",
        client_name="Zubair Qureshi",
        client_phone="03211234567",
        property_title="5 Marla House in Johar Town, Lahore",
        property_id="PROP-1116",
        date_str="Tomorrow",
        time_str="3:00 PM",
        requirements="Budget under 1.5 crore, ready to move in",
        notes="Qualified by AI voice agent",
        calendar_link="https://calendar.google.com",
        event_type="booking",
    )
    assert result["email_id"].startswith("eml_")
    assert result["recipient"] in ("sana.malik@realestatehub.pk", "umersahi5p@gmail.com")
    assert "Zubair Qureshi" in result["plain_text"]
    assert "Johar Town" in result["plain_text"]

    emails = list_sent_emails(limit=10)
    assert any(e["email_id"] == result["email_id"] for e in emails)


def test_task3_appointment_management_lifecycle():
    """Task 3: Full booking -> rescheduling -> cancellation workflow."""
    # 1. Booking
    booking = book_appointment(
        client_name="Tariq Mansoor",
        client_phone="03335554433",
        employee_name="Bilal Chaudhry",
        property_title="1 Kanal Luxury House in Bahria Town, Rawalpindi",
        property_id="PROP-1504",
        date_str="Tomorrow",
        time_str="2:00 PM",
        city="Rawalpindi",
        budget="3.5 crore",
        notes="Wants to inspect finishing and basement",
    )
    assert booking["ok"] is True
    assert booking["action"] == "booked"
    appt_id = booking["appointment_id"]
    lead_id = booking["lead_id"]
    assert appt_id is not None
    assert lead_id is not None

    # Check CRM appointment record
    appt = get_appointment(appt_id)
    assert appt is not None
    assert appt["status"] == "scheduled"
    assert appt["agent_name"] == "Bilal Chaudhry"

    # 2. Rescheduling
    resched = reschedule_appointment(
        appointment_id=appt_id,
        new_date_str="2026-09-08",
        new_time_str="6:00 PM",
        reason="Client requested evening slot",
    )
    assert resched["ok"] is True
    assert resched["action"] == "rescheduled"
    appt_after_resched = get_appointment(appt_id)
    assert appt_after_resched["status"] == "rescheduled"
    assert appt_after_resched["date_str"] == "2026-09-08"

    # 3. Cancellation
    cancel = cancel_appointment(appointment_id=appt_id, reason="Client purchased another property")
    assert cancel["ok"] is True
    assert cancel["action"] == "cancelled"
    appt_after_cancel = get_appointment(appt_id)
    assert appt_after_cancel["status"] == "cancelled"


def test_task4_n8n_workflow_json():
    """Task 4: Validate n8n workflow definition and retry policies."""
    workflow_path = Path(__file__).parent.parent.parent / "workflows" / "realestate_voice_business_workflow.json"
    assert workflow_path.exists()

    data = json.loads(workflow_path.read_text(encoding="utf-8"))
    assert "nodes" in data
    assert "connections" in data

    node_names = [n["name"] for n in data["nodes"]]
    assert "Webhook - Call / Voice Event" in node_names
    assert "Classify Call Intent" in node_names
    assert "Route by Intent" in node_names
    assert "Book Appointment & Calendar" in node_names
    assert "Reschedule Appointment API" in node_names
    assert "Cancel Appointment API" in node_names
    assert "Log CRM Transcript & Leads" in node_names
    assert "Error & Failure Handler" in node_names

    # Verify retry configuration on HTTP nodes
    book_node = next(n for n in data["nodes"] if n["name"] == "Book Appointment & Calendar")
    options = book_node["parameters"]["options"]
    assert options.get("retryOnFail") is True
    assert options.get("maxTries") == 3


def test_task5_crm_logging():
    """Task 5: Test CRM Leads, Transcripts, Reminders, and Dashboard Stats."""
    # Test Lead creation & update
    lead_id = upsert_lead(
        client_name="Rashid Minhas",
        phone="03451122334",
        city="Islamabad",
        budget="2.2 crore",
        property_type="House",
        purpose="For Sale",
        stage="Qualified",
        preferences={"marla": 7, "sector": "G-13"},
    )
    assert lead_id is not None
    leads = list_leads(limit=20)
    assert any(l["lead_id"] == lead_id for l in leads)

    # Test Call Transcript Logging
    transcript_id = log_call_transcript(
        call_id="call_test_001",
        lead_id=lead_id,
        caller_phone="03451122334",
        transcript_text="Customer asked for 7 marla house in G-13 Islamabad, budget 2.2 crore.",
        summary="Qualified buyer looking for G-13 house",
        sentiment="Positive",
        turns=[
            {"role": "user", "text": "Islamabad mein ghar chahiye"},
            {"role": "assistant", "text": "Kis sector mein dekh rahe hain?"}
        ],
    )
    assert transcript_id is not None
    transcripts = list_transcripts(limit=20)
    assert any(t["transcript_id"] == transcript_id for t in transcripts)

    # Test Follow-up Reminders
    rem_id = create_reminder(
        lead_id=lead_id,
        client_name="Rashid Minhas",
        reminder_date="Tomorrow",
        note="Call back with G-13 verified listings",
    )
    assert rem_id is not None
    reminders = list_reminders(limit=20)
    assert any(r["reminder_id"] == rem_id for r in reminders)

    dismiss_reminder(rem_id)
    active_reminders = list_reminders(limit=20, status="pending")
    assert not any(r["reminder_id"] == rem_id for r in active_reminders)

    # Test Dashboard Stats
    stats = get_crm_dashboard_stats()
    assert "total_leads" in stats
    assert "active_appointments" in stats
    assert "total_calls" in stats
    assert stats["total_leads"] >= 1


if __name__ == "__main__":
    print("Running Task 1: Google Calendar Integration Test...")
    test_task1_calendar_integration()
    print("[PASS] Task 1 PASSED")

    print("Running Task 2: Email Automation Test...")
    test_task2_email_automation()
    print("[PASS] Task 2 PASSED")

    print("Running Task 3: Appointment Management Lifecycle Test...")
    test_task3_appointment_management_lifecycle()
    print("[PASS] Task 3 PASSED")

    print("Running Task 4: n8n Workflow JSON & Retry Validation Test...")
    test_task4_n8n_workflow_json()
    print("[PASS] Task 4 PASSED")

    print("Running Task 5: CRM Logging & Persistence Test...")
    test_task5_crm_logging()
    print("[PASS] Task 5 PASSED")

    print("\n=======================================================")
    print("ALL 5 BUSINESS AUTOMATION TASKS PASSED PERFECTLY!")
    print("=======================================================")

