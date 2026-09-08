"""Task 1: Google Calendar Integration.

Provides calendar event management for RealEstate Hub visits and appointments:
- Direct Google Calendar API client (when credentials/calendar ID provided)
- Built-in universal fallback generating standard iCal (.ics) data and direct
  Google Calendar web action links (https://calendar.google.com/calendar/render...)
  so calendar invitations and clickable browser links work with zero external dependencies.

Included Event Details:
- Client name
- Phone
- Employee (Assigned RealEstate Hub Agent)
- Property
- Date
- Time
- Meeting notes
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.parse
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from config import PROJECT_ROOT

CALENDAR_STORAGE_DIR = PROJECT_ROOT / "storage" / "calendar"
CALENDAR_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CalendarEvent:
    event_id: str
    client_name: str
    phone: str
    employee: str
    property_title: str
    property_id: str
    date_str: str
    time_str: str
    notes: str
    google_calendar_link: str
    status: str = "confirmed"
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _generate_google_calendar_web_link(
    title: str,
    details: str,
    location: str,
    start_dt: datetime.datetime,
    duration_minutes: int = 45,
) -> str:
    """Generate a one-click direct Google Calendar event creation URL."""
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    fmt = "%Y%m%dT%H%M%SZ"
    dates_param = f"{start_dt.strftime(fmt)}/{end_dt.strftime(fmt)}"
    params = {
        "action": "TEMPLATE",
        "text": title,
        "details": details,
        "location": location,
        "dates": dates_param,
    }
    return f"https://calendar.google.com/calendar/render?{urllib.parse.urlencode(params)}"


def _generate_ics_content(
    event_id: str,
    title: str,
    details: str,
    location: str,
    start_dt: datetime.datetime,
    duration_minutes: int = 45,
) -> str:
    """Generate standard iCalendar (.ics) RFC 5545 format."""
    end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
    fmt = "%Y%m%dT%H%M%SZ"
    now_fmt = datetime.datetime.now(datetime.timezone.utc).strftime(fmt)
    clean_details = details.replace("\n", "\\n")
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//RealEstate Hub//Voice Agent Booking//EN\r\n"
        "CALSCALE:GREGORIAN\r\n"
        "METHOD:REQUEST\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{event_id}@realestatehub.pk\r\n"
        f"DTSTAMP:{now_fmt}\r\n"
        f"DTSTART:{start_dt.strftime(fmt)}\r\n"
        f"DTEND:{end_dt.strftime(fmt)}\r\n"
        f"SUMMARY:{title}\r\n"
        f"DESCRIPTION:{clean_details}\r\n"
        f"LOCATION:{location}\r\n"
        "STATUS:CONFIRMED\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )


def _parse_event_datetime(date_str: str, time_str: str) -> datetime.datetime:
    """Parse natural or ISO date/time strings into a timezone-aware datetime."""
    now = datetime.datetime.now(datetime.timezone.utc)
    target_date = now.date()

    d_clean = date_str.lower().strip()
    if "kal" in d_clean or "tomorrow" in d_clean or "کل" in d_clean:
        target_date = (now + datetime.timedelta(days=1)).date()
    elif "parso" in d_clean or "day after" in d_clean or "پرسوں" in d_clean:
        target_date = (now + datetime.timedelta(days=2)).date()
    elif "aaj" in d_clean or "today" in d_clean or "آج" in d_clean:
        target_date = now.date()
    else:
        # Try standard YYYY-MM-DD
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%B %d", "%b %d"):
            try:
                parsed = datetime.datetime.strptime(date_str.strip(), fmt)
                if parsed.year == 1900:
                    parsed = parsed.replace(year=now.year)
                target_date = parsed.date()
                break
            except ValueError:
                continue

    # Default to 15:00 (3 PM) if unspecified
    hour, minute = 15, 0
    t_clean = time_str.lower().strip()
    if "shaam" in t_clean or "pm" in t_clean or "شام" in t_clean or "evening" in t_clean:
        hour = 17
    elif "subah" in t_clean or "am" in t_clean or "صبح" in t_clean or "morning" in t_clean:
        hour = 11
    elif "dopahar" in t_clean or "noon" in t_clean or "دوپہر" in t_clean:
        hour = 14

    import re
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje|bjy|بجے)?", t_clean)
    if m:
        num = int(m.group(1))
        min_part = int(m.group(2)) if m.group(2) else 0
        ampm = m.group(3) or ""
        if ampm in ("pm", "shaam", "شام", "baje", "bjy", "بجے") and num < 12 and (num in (1, 2, 3, 4, 5, 6, 7, 8, 9)):
            num += 12
        elif ampm in ("am", "subah", "صبح") and num == 12:
            num = 0
        if 0 <= num <= 23:
            hour = num
        if 0 <= min_part <= 59:
            minute = min_part

    return datetime.datetime(
        target_date.year, target_date.month, target_date.day,
        hour, minute, 0, tzinfo=datetime.timezone.utc
    )


def create_calendar_event(
    client_name: str,
    phone: str,
    employee: str,
    property_title: str,
    property_id: str,
    date_str: str,
    time_str: str,
    notes: str = "",
) -> CalendarEvent:
    """Create a calendar event with all required business fields."""
    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start_dt = _parse_event_datetime(date_str, time_str)

    title = f"Property Visit: {property_title or property_id} with {client_name}"
    details = (
        f"RealEstate Hub Scheduled Property Visit\n"
        f"-----------------------------------------\n"
        f"Client Name: {client_name}\n"
        f"Client Phone: {phone}\n"
        f"Assigned Consultant / Agent: {employee}\n"
        f"Property: {property_title} ({property_id})\n"
        f"Scheduled Date: {date_str}\n"
        f"Scheduled Time: {time_str}\n"
        f"Meeting Notes: {notes or 'Customer inquiry from Voice Agent'}\n"
    )
    location = property_title or "RealEstate Hub Site Visit"

    # Attempt Google Calendar API if credentials are configured
    gcal_link = _generate_google_calendar_web_link(title, details, location, start_dt)

    google_creds = os.getenv("GOOGLE_CALENDAR_CREDENTIALS")
    google_cal_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
    if google_creds and os.path.exists(google_creds):
        try:
            # When google-api-python-client is configured with service account
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            creds = service_account.Credentials.from_service_account_file(
                google_creds, scopes=["https://www.googleapis.com/auth/calendar"]
            )
            service = build("calendar", "v3", credentials=creds)
            body = {
                "summary": title,
                "description": details,
                "location": location,
                "start": {"dateTime": start_dt.isoformat()},
                "end": {"dateTime": (start_dt + datetime.timedelta(minutes=45)).isoformat()},
            }
            res = service.events().insert(calendarId=google_cal_id, body=body).execute()
            if res.get("htmlLink"):
                gcal_link = res["htmlLink"]
        except Exception:
            pass

    event = CalendarEvent(
        event_id=event_id,
        client_name=client_name or "Valued Client",
        phone=phone or "Not Provided",
        employee=employee or "Ahmed Raza",
        property_title=property_title,
        property_id=property_id or "PROP-General",
        date_str=date_str,
        time_str=time_str,
        notes=notes,
        google_calendar_link=gcal_link,
        status="confirmed",
        created_at=created_at,
    )

    # Save to local calendar audit store (.json and .ics)
    ics_data = _generate_ics_content(event_id, title, details, location, start_dt)
    (CALENDAR_STORAGE_DIR / f"{event_id}.json").write_text(json.dumps(event.to_dict(), indent=2), encoding="utf-8")
    (CALENDAR_STORAGE_DIR / f"{event_id}.ics").write_text(ics_data, encoding="utf-8")

    return event


def update_calendar_event(
    event_id: str,
    date_str: str,
    time_str: str,
    notes: str = "",
) -> CalendarEvent | None:
    """Reschedule or update an existing calendar event."""
    file_path = CALENDAR_STORAGE_DIR / f"{event_id}.json"
    if not file_path.exists():
        # Generate on the fly if not in local storage
        event = CalendarEvent(
            event_id=event_id,
            client_name="Valued Client",
            phone="Not Provided",
            employee="Ahmed Raza",
            property_title="RealEstate Hub Property",
            property_id="PROP-0000",
            date_str=date_str,
            time_str=time_str,
            notes=notes,
            google_calendar_link="",
            status="rescheduled",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
    else:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        data["date_str"] = date_str
        data["time_str"] = time_str
        data["status"] = "rescheduled"
        if notes:
            data["notes"] = notes
        event = CalendarEvent(**data)

    start_dt = _parse_event_datetime(date_str, time_str)
    title = f"Rescheduled: Visit for {event.property_title} with {event.client_name}"
    details = (
        f"RESCHEDULED Property Visit\n"
        f"---------------------------\n"
        f"Client: {event.client_name} ({event.phone})\n"
        f"Agent: {event.employee}\n"
        f"Property: {event.property_title}\n"
        f"New Date: {date_str} at {time_str}\n"
        f"Notes: {event.notes}\n"
    )
    event.google_calendar_link = _generate_google_calendar_web_link(title, details, event.property_title, start_dt)

    file_path.write_text(json.dumps(event.to_dict(), indent=2), encoding="utf-8")
    ics_data = _generate_ics_content(event_id, title, details, event.property_title, start_dt)
    (CALENDAR_STORAGE_DIR / f"{event_id}.ics").write_text(ics_data, encoding="utf-8")

    return event


def delete_calendar_event(event_id: str, reason: str = "Cancelled by client") -> bool:
    """Cancel and mark a calendar event as cancelled."""
    file_path = CALENDAR_STORAGE_DIR / f"{event_id}.json"
    if file_path.exists():
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            data["status"] = "cancelled"
            data["cancellation_reason"] = reason
            file_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False
    return True
