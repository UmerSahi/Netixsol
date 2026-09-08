"""Task 2: Email Automation.

Automatically sends notifications to assigned RealEstate Hub employees/agents for:
- New Appointment Bookings
- Rescheduled Meetings
- Cancelled Visits

Included Details:
- Meeting time & date
- Property specs (type, marla, locality, city, price)
- Client details (name, phone, contact)
- Client requirements & preferences
- Direct Add-to-Calendar link

Features:
- Live SMTP dispatcher (configurable via .env: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS)
- Persistent local email audit store (storage/emails/) for auditing, testing, and offline verification.
"""
from __future__ import annotations

import datetime
import json
import os
import smtplib
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from config import PROJECT_ROOT

EMAIL_STORAGE_DIR = PROJECT_ROOT / "storage" / "emails"
EMAIL_STORAGE_DIR.mkdir(parents=True, exist_ok=True)

AGENT_EMAIL_ROSTER = {
    "Ahmed Raza": "ahmed.raza@realestatehub.pk",
    "Sana Malik": "sana.malik@realestatehub.pk",
    "Bilal Chaudhry": "bilal.chaudhry@realestatehub.pk",
    "Ayesha Farooq": "ayesha.farooq@realestatehub.pk",
    "Usman Tariq": "usman.tariq@realestatehub.pk",
    "Hina Shaikh": "hina.shaikh@realestatehub.pk",
    "Faisal Mehmood": "faisal.mehmood@realestatehub.pk",
    "Mahnoor Iqbal": "mahnoor.iqbal@realestatehub.pk",
}
DEFAULT_SENDER = os.getenv("NOTIFICATION_SENDER", "appointments@realestatehub.pk")
TEST_OVERRIDE_EMAIL = os.getenv("EMPLOYEE_NOTIFICATION_EMAIL", "").strip()


def get_agent_email(agent_name: str, ignore_override: bool = False) -> str:
    """Resolve employee email address from agent roster or test override."""
    override = os.getenv("EMPLOYEE_NOTIFICATION_EMAIL", "").strip()
    if override and not ignore_override:
        return override
    clean = agent_name.strip()
    if clean in AGENT_EMAIL_ROSTER:
        return AGENT_EMAIL_ROSTER[clean]
    # Fallback to normalized name email
    slug = clean.lower().replace(" ", ".")
    return f"{slug}@realestatehub.pk"


def _build_html_email(
    subject: str,
    headline: str,
    action_color: str,
    employee_name: str,
    client_name: str,
    client_phone: str,
    property_title: str,
    property_id: str,
    date_str: str,
    time_str: str,
    requirements: str,
    notes: str,
    calendar_link: str = "",
) -> str:
    """Generate professional responsive HTML email template."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 20px; color: #1e293b; }}
    .card {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }}
    .header {{ background: {action_color}; color: #ffffff; padding: 24px; text-align: center; }}
    .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; }}
    .header p {{ margin: 6px 0 0 0; opacity: 0.9; font-size: 14px; }}
    .content {{ padding: 28px; }}
    .section {{ margin-bottom: 20px; }}
    .section-title {{ font-size: 13px; font-weight: 700; text-transform: uppercase; color: #64748b; margin-bottom: 8px; letter-spacing: 0.5px; }}
    .info-grid {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 14px 18px; }}
    .info-row {{ display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed #e2e8f0; font-size: 14px; }}
    .info-row:last-child {{ border-bottom: none; }}
    .info-label {{ color: #64748b; font-weight: 500; }}
    .info-value {{ color: #0f172a; font-weight: 600; text-align: right; }}
    .btn-container {{ text-align: center; margin-top: 24px; }}
    .btn {{ display: inline-block; background: #0284c7; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px; }}
    .footer {{ background: #f1f5f9; padding: 16px; text-align: center; font-size: 12px; color: #94a3b8; border-top: 1px solid #e2e8f0; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="header">
      <h1>{headline}</h1>
      <p>RealEstate Hub Voice Assistant Automation</p>
    </div>
    <div class="content">
      <p style="font-size: 15px; margin-top: 0;">Hello <strong>{employee_name}</strong>,</p>
      <p style="font-size: 14px; color: #475569;">A property visit appointment has been scheduled and assigned to you via the voice agent.</p>
      
      <div class="section">
        <div class="section-title">📅 Meeting Schedule</div>
        <div class="info-grid">
          <div class="info-row"><span class="info-label">Date:</span><span class="info-value">{date_str}</span></div>
          <div class="info-row"><span class="info-label">Time:</span><span class="info-value">{time_str}</span></div>
          <div class="info-row"><span class="info-label">Assigned Agent:</span><span class="info-value">{employee_name}</span></div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">🏡 Property Details</div>
        <div class="info-grid">
          <div class="info-row"><span class="info-label">Property:</span><span class="info-value">{property_title}</span></div>
          <div class="info-row"><span class="info-label">Listing ID:</span><span class="info-value">{property_id}</span></div>
        </div>
      </div>

      <div class="section">
        <div class="section-title">👤 Client Details & Requirements</div>
        <div class="info-grid">
          <div class="info-row"><span class="info-label">Client Name:</span><span class="info-value">{client_name}</span></div>
          <div class="info-row"><span class="info-label">Phone:</span><span class="info-value">{client_phone}</span></div>
          <div class="info-row"><span class="info-label">Requirements:</span><span class="info-value">{requirements or 'Looking for property'}</span></div>
          <div class="info-row"><span class="info-label">Call Notes:</span><span class="info-value">{notes or 'Voice Agent Qualified Lead'}</span></div>
        </div>
      </div>

      {f'''<div class="btn-container">
        <a href="{calendar_link}" class="btn" target="_blank">➕ Add to Google Calendar</a>
      </div>''' if calendar_link else ''}
    </div>
    <div class="footer">
      RealEstate Hub Automated Business Dispatcher &bull; Confirmed via Voice AI
    </div>
  </div>
</body>
</html>"""


def send_employee_appointment_email(
    employee_name: str,
    client_name: str,
    client_phone: str,
    property_title: str,
    property_id: str,
    date_str: str,
    time_str: str,
    requirements: str = "",
    notes: str = "",
    calendar_link: str = "",
    event_type: str = "booking",  # "booking", "reschedule", "cancellation"
) -> dict[str, Any]:
    """Send appointment notification email to assigned employee/agent."""
    recipient_email = get_agent_email(employee_name)
    email_id = f"eml_{uuid.uuid4().hex[:12]}"
    sent_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if event_type == "reschedule":
        subject = f"[RESCHEDULED] Visit for {property_title} with {client_name}"
        headline = "🗓️ Appointment Rescheduled"
        action_color = "#d97706"  # Amber
    elif event_type == "cancellation":
        subject = f"[CANCELLED] Visit for {property_title} with {client_name}"
        headline = "❌ Appointment Cancelled"
        action_color = "#dc2626"  # Red
    else:
        subject = f"[NEW VISIT] {property_title} on {date_str} with {client_name}"
        headline = "✨ New Property Visit Scheduled"
        action_color = "#0f766e"  # Teal

    html_content = _build_html_email(
        subject=subject,
        headline=headline,
        action_color=action_color,
        employee_name=employee_name,
        client_name=client_name,
        client_phone=client_phone,
        property_title=property_title,
        property_id=property_id,
        date_str=date_str,
        time_str=time_str,
        requirements=requirements,
        notes=notes,
        calendar_link=calendar_link,
    )

    plain_text = (
        f"{headline}\n"
        f"===================================\n"
        f"Employee: {employee_name} ({recipient_email})\n"
        f"Client: {client_name} (Phone: {client_phone})\n"
        f"Property: {property_title} ({property_id})\n"
        f"Date & Time: {date_str} at {time_str}\n"
        f"Requirements: {requirements}\n"
        f"Notes: {notes}\n"
        f"Calendar Link: {calendar_link}\n"
    )

    # Attempt live SMTP if configured in .env
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    smtp_delivered = False
    smtp_error = None

    if smtp_host and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = DEFAULT_SENDER
            msg["To"] = recipient_email
            msg.attach(MIMEText(plain_text, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=5.0) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.sendmail(DEFAULT_SENDER, [recipient_email], msg.as_string())
            smtp_delivered = True
        except Exception as e:
            smtp_error = str(e)

    record = {
        "email_id": email_id,
        "event_type": event_type,
        "recipient": recipient_email,
        "employee_name": employee_name,
        "client_name": client_name,
        "client_phone": client_phone,
        "property_title": property_title,
        "property_id": property_id,
        "date_str": date_str,
        "time_str": time_str,
        "subject": subject,
        "plain_text": plain_text,
        "sent_at": sent_at,
        "smtp_delivered": smtp_delivered,
        "smtp_error": smtp_error,
    }

    # Store audit records (.json and .html) for dashboard viewing and validation
    (EMAIL_STORAGE_DIR / f"{email_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    (EMAIL_STORAGE_DIR / f"{email_id}.html").write_text(html_content, encoding="utf-8")

    return record


def list_sent_emails(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve history of dispatched email notifications."""
    emails = []
    files = sorted(EMAIL_STORAGE_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
    for p in files[:limit]:
        try:
            emails.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return emails
