# RealEstate Hub — n8n Business Workflow Automation

This directory contains the production-ready n8n workflow for **Week 7 — Day 4: Workflows, Scheduling & Business Automation**.

## Workflow Overview: Call → Intent → Match → Appointment → Calendar → Email → CRM Update

```
  [Webhook Trigger] (Call / Voice Server Event)
          │
          ▼
  [Intent Classifier] (Detects Booking / Reschedule / Cancel / Inquiry)
          │
    ┌─────┴──────────────────┬──────────────────────┐
    ▼                        ▼                      ▼
[Book Appointment]   [Reschedule Appt]      [Cancel Appt]
    │                        │                      │
    └─────┬──────────────────┴──────────────────────┘
          │
          ▼
  [Calendar & Email Sync] (Google Calendar + Employee Dispatcher)
          │
          ▼
  [CRM Update] (Leads, Transcripts, Follow-up Reminders)
          │
          ▼
  [Error & Retry Handler] (Exponential backoff 3x + Dead-letter Alerts)
```

## Features Included
1. **Failure & Retry Handling**: Every external HTTP node is configured with 3 retries and 2000ms exponential wait between attempts.
2. **Multi-Intent Routing**: Supports new visit bookings, date/time rescheduling, and meeting cancellations.
3. **Automated Calendar & Email Sync**: Generates Google Calendar links and emails the assigned employee immediately.
4. **CRM Logging**: Records call transcripts, updates lead status, and sets follow-up reminders.

## How to Import into n8n
1. Open your n8n workspace (e.g. `http://localhost:5678`).
2. Click **Workflows** &rarr; **Add Workflow** &rarr; **Import from File...**.
3. Select `realestate_voice_business_workflow.json`.
4. In your n8n environment variables, set `BACKEND_URL=http://localhost:8000` (or your ngrok URL).
5. Activate the workflow!
