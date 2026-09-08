"""Task 3: Appointment Management.

Coordinates the complete business lifecycle for property visits:
- Booking: Lead CRM Record -> Calendar Event -> Employee Email Notification -> Follow-up Reminder
- Rescheduling: Calendar Update -> Employee Reschedule Email -> CRM History Update
- Cancellation: Calendar Event Cancel -> Employee Cancellation Email -> CRM Status Update
"""
from __future__ import annotations

import datetime
from typing import Any

from calendar_service import create_calendar_event, delete_calendar_event, update_calendar_event
from crm_service import (
    create_reminder,
    get_appointment,
    record_appointment,
    upsert_lead,
)
from email_service import send_employee_appointment_email


def book_appointment(
    client_name: str,
    client_phone: str,
    employee_name: str,
    property_title: str,
    property_id: str,
    date_str: str,
    time_str: str,
    client_email: str = "",
    city: str = "",
    budget: str = "",
    property_type: str = "",
    purpose: str = "For Sale",
    requirements: str = "",
    notes: str = "",
) -> dict[str, Any]:
    """Book a new property visit appointment across CRM, Calendar, and Email."""
    # 1. Upsert Lead in CRM
    lead_id = upsert_lead(
        client_name=client_name or "Valued Client",
        phone=client_phone or "Not Provided",
        email=client_email,
        city=city,
        budget=budget,
        property_type=property_type,
        purpose=purpose,
        stage="Meeting Scheduled",
        preferences={
            "property_title": property_title,
            "property_id": property_id,
            "requirements": requirements,
            "notes": notes,
        },
    )

    # 2. Create Google Calendar Event & iCal link
    cal_event = create_calendar_event(
        client_name=client_name or "Valued Client",
        phone=client_phone or "Not Provided",
        employee=employee_name or "Ahmed Raza",
        property_title=property_title,
        property_id=property_id,
        date_str=date_str,
        time_str=time_str,
        notes=notes,
    )

    # 3. Record Appointment in CRM
    appointment_id = record_appointment(
        lead_id=lead_id,
        client_name=client_name or "Valued Client",
        client_phone=client_phone or "Not Provided",
        property_id=property_id or "PROP-General",
        property_title=property_title,
        agent_name=employee_name or "Ahmed Raza",
        date_str=date_str,
        time_str=time_str,
        status="scheduled",
        calendar_event_id=cal_event.event_id,
        calendar_link=cal_event.google_calendar_link,
        notes=notes,
    )

    # 4. Dispatch Email Notification to Assigned Employee
    email_result = send_employee_appointment_email(
        employee_name=employee_name or "Ahmed Raza",
        client_name=client_name or "Valued Client",
        client_phone=client_phone or "Not Provided",
        property_title=property_title,
        property_id=property_id,
        date_str=date_str,
        time_str=time_str,
        requirements=requirements or f"Interested in {property_title}",
        notes=notes,
        calendar_link=cal_event.google_calendar_link,
        event_type="booking",
    )

    # 5. Create Follow-up Reminder for Agent
    reminder_id = create_reminder(
        lead_id=lead_id,
        client_name=client_name or "Valued Client",
        reminder_date=date_str,
        note=f"Conduct site visit for {property_title} at {time_str} with {client_name} ({client_phone})",
        appointment_id=appointment_id,
    )

    return {
        "ok": True,
        "action": "booked",
        "appointment_id": appointment_id,
        "lead_id": lead_id,
        "calendar_event_id": cal_event.event_id,
        "google_calendar_link": cal_event.google_calendar_link,
        "email_id": email_result["email_id"],
        "assigned_employee": employee_name or "Ahmed Raza",
        "date_str": date_str,
        "time_str": time_str,
        "reminder_id": reminder_id,
    }


def reschedule_appointment(
    appointment_id: str,
    new_date_str: str,
    new_time_str: str,
    reason: str = "",
) -> dict[str, Any]:
    """Reschedule an existing appointment and update Calendar & Email notifications."""
    existing = get_appointment(appointment_id)
    if not existing:
        return {"ok": False, "error": f"Appointment {appointment_id} not found."}

    # 1. Update Calendar Event
    cal_event_id = existing.get("calendar_event_id") or f"evt_{appointment_id}"
    notes = f"Rescheduled: {reason}" if reason else existing.get("notes", "")
    updated_cal = update_calendar_event(
        event_id=cal_event_id,
        date_str=new_date_str,
        time_str=new_time_str,
        notes=notes,
    )
    gcal_link = updated_cal.google_calendar_link if updated_cal else existing.get("calendar_link", "")

    # 2. Update Appointment in CRM
    record_appointment(
        lead_id=existing.get("lead_id", ""),
        client_name=existing.get("client_name", "Valued Client"),
        client_phone=existing.get("client_phone", "Not Provided"),
        property_id=existing.get("property_id", ""),
        property_title=existing.get("property_title", ""),
        agent_name=existing.get("agent_name", "Ahmed Raza"),
        date_str=new_date_str,
        time_str=new_time_str,
        status="rescheduled",
        calendar_event_id=cal_event_id,
        calendar_link=gcal_link,
        notes=notes,
        appointment_id=appointment_id,
    )

    # 3. Dispatch Rescheduled Email to Assigned Employee
    email_result = send_employee_appointment_email(
        employee_name=existing.get("agent_name", "Ahmed Raza"),
        client_name=existing.get("client_name", "Valued Client"),
        client_phone=existing.get("client_phone", "Not Provided"),
        property_title=existing.get("property_title", "RealEstate Hub Property"),
        property_id=existing.get("property_id", ""),
        date_str=new_date_str,
        time_str=new_time_str,
        requirements=f"Rescheduled visit (Old: {existing.get('date_str')} at {existing.get('time_str')})",
        notes=notes,
        calendar_link=gcal_link,
        event_type="reschedule",
    )

    return {
        "ok": True,
        "action": "rescheduled",
        "appointment_id": appointment_id,
        "new_date_str": new_date_str,
        "new_time_str": new_time_str,
        "calendar_link": gcal_link,
        "email_id": email_result["email_id"],
    }


def cancel_appointment(
    appointment_id: str,
    reason: str = "Client requested cancellation",
) -> dict[str, Any]:
    """Cancel an appointment and notify Calendar & Employee."""
    existing = get_appointment(appointment_id)
    if not existing:
        return {"ok": False, "error": f"Appointment {appointment_id} not found."}

    # 1. Update Calendar
    cal_event_id = existing.get("calendar_event_id") or f"evt_{appointment_id}"
    delete_calendar_event(cal_event_id, reason=reason)

    # 2. Update Appointment in CRM
    record_appointment(
        lead_id=existing.get("lead_id", ""),
        client_name=existing.get("client_name", "Valued Client"),
        client_phone=existing.get("client_phone", "Not Provided"),
        property_id=existing.get("property_id", ""),
        property_title=existing.get("property_title", ""),
        agent_name=existing.get("agent_name", "Ahmed Raza"),
        date_str=existing.get("date_str", ""),
        time_str=existing.get("time_str", ""),
        status="cancelled",
        calendar_event_id=cal_event_id,
        calendar_link=existing.get("calendar_link", ""),
        notes=f"CANCELLED: {reason}",
        appointment_id=appointment_id,
    )

    # 3. Dispatch Cancellation Email to Assigned Employee
    email_result = send_employee_appointment_email(
        employee_name=existing.get("agent_name", "Ahmed Raza"),
        client_name=existing.get("client_name", "Valued Client"),
        client_phone=existing.get("client_phone", "Not Provided"),
        property_title=existing.get("property_title", "RealEstate Hub Property"),
        property_id=existing.get("property_id", ""),
        date_str=existing.get("date_str", ""),
        time_str=existing.get("time_str", ""),
        requirements="Appointment Cancelled",
        notes=f"Reason: {reason}",
        calendar_link="",
        event_type="cancellation",
    )

    return {
        "ok": True,
        "action": "cancelled",
        "appointment_id": appointment_id,
        "reason": reason,
        "email_id": email_result["email_id"],
    }
