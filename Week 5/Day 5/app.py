"""
app.py
======
Task 4 -- FastAPI wrapper around the agent in `agent_core.py`.

Run locally:
    uvicorn app:app --reload --port 8000

Endpoints:
    POST /tickets            submit a new ticket, get the agent's structured result
    POST /tickets/{id}/approve   resume a paused (human-approval-gated) ticket
    GET  /healthz             liveness check

Every request is logged as one JSON line to `agent_runs.log` with the
fields a real monitoring setup would want: timestamp, ticket id,
category, status, tool calls made, token usage, latency, and any
error -- see Task 4's monitoring checklist (monitoring_checklist.md)
for what to do with these once they're flowing.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, EmailStr, Field

from agent_core import GRAPH, ToolError, run_ticket
from langgraph.types import Command

# ---------------------------------------------------------------------
# Structured logging -- one JSON object per line, ready for shipping
# to any log aggregator (CloudWatch, Datadog, Loki, ...).
# ---------------------------------------------------------------------

logger = logging.getLogger("agent_runs")
logger.setLevel(logging.INFO)
_handler = logging.FileHandler("agent_runs.log")
_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler())  # also echo to stdout


def log_run(event: dict) -> None:
    logger.info(json.dumps(event, default=str))


app = FastAPI(
    title="Web3Geeks Support Triage Agent",
    description="Capstone: LangGraph ticket-triage agent behind a FastAPI wrapper.",
    version="1.0.0",
)

if not os.environ.get("GEMINI_API_KEY"):
    # Not fatal at import time (so /healthz still works and the process can
    # start), but every ticket will fail with a clear RuntimeError from
    # agent_core.get_llm_client() until this is set -- this agent has no
    # offline fallback by design. Loud at startup beats a silent surprise
    # on the first real ticket.
    logger.warning(json.dumps({
        "event": "startup_warning",
        "message": "GEMINI_API_KEY is not set -- all /tickets requests will fail "
                    "until it is. This agent has no offline fallback by design.",
    }))


class TicketRequest(BaseModel):
    customer_email: EmailStr
    text: str = Field(..., min_length=1, description="Raw ticket / support message text")
    auto_approve_refunds: Optional[bool] = Field(
        default=None,
        description=(
            "For demos/tests only: True/False resolves the human-approval gate "
            "immediately. Omit (null) in a real deployment so the ticket pauses "
            "at the gate for a human to review via POST /tickets/{id}/approve."
        ),
    )


class ApprovalRequest(BaseModel):
    approved: bool


class TicketResponse(BaseModel):
    ticket_id: str
    status: str
    category: Optional[str] = None
    final_response: Optional[str] = None
    requires_human_approval: bool = False
    errors: list[str] = []


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/tickets", response_model=TicketResponse)
def submit_ticket(req: TicketRequest):
    ticket_id = str(uuid.uuid4())
    t0 = time.time()
    error_detail: Optional[str] = None

    try:
        result = run_ticket(
            raw_text=req.text,
            customer_email=req.customer_email,
            auto_approve_refunds=req.auto_approve_refunds,
            thread_id=ticket_id,
        )
    except ToolError as e:
        # Tool-layer failures are already handled gracefully inside the graph;
        # this except only catches anything that slipped through, so the API
        # never 500s on a bad ticket -- it returns a clean escalation instead.
        error_detail = str(e)
        result = {
            "status": "escalated",
            "category": None,
            "final_response": "This ticket needs manual review due to a system error.",
            "errors": [error_detail],
        }
    except Exception as e:  # last-resort guard so the API contract always holds
        error_detail = f"{type(e).__name__}: {e}"
        result = {
            "status": "escalated",
            "category": None,
            "final_response": "This ticket needs manual review due to a system error.",
            "errors": [error_detail],
        }
    finally:
        log_run({
            "event": "ticket_submitted",
            "ticket_id": ticket_id,
            "timestamp": time.time(),
            "latency_ms": round((time.time() - t0) * 1000, 1),
            "category": result.get("category"),
            "status": result.get("status"),
            "input_tokens": result.get("input_tokens", 0),
            "output_tokens": result.get("output_tokens", 0),
            "tool_calls": [e["node"] for e in result.get("trace", []) if e["node"] in ("billing_lookup",)],
            "error": error_detail,
        })

    paused = "requires_human_approval" in result and result.get("human_approved") is None
    return TicketResponse(
        ticket_id=ticket_id,
        status="pending_approval" if paused else result.get("status", "unknown"),
        category=result.get("category"),
        final_response=result.get("final_response"),
        requires_human_approval=bool(result.get("requires_human_approval", False)),
        errors=result.get("errors", []),
    )


@app.post("/tickets/{ticket_id}/approve", response_model=TicketResponse)
def approve_ticket(ticket_id: str, req: ApprovalRequest):
    """Resumes a ticket that's paused at the human-approval interrupt."""
    t0 = time.time()
    config = {"configurable": {"thread_id": ticket_id}}
    try:
        result = GRAPH.invoke(Command(resume=req.approved), config=config)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"No paused ticket with that id ({e})")

    log_run({
        "event": "ticket_approved",
        "ticket_id": ticket_id,
        "timestamp": time.time(),
        "latency_ms": round((time.time() - t0) * 1000, 1),
        "approved": req.approved,
        "status": result.get("status"),
    })

    return TicketResponse(
        ticket_id=ticket_id,
        status=result.get("status", "unknown"),
        category=result.get("category"),
        final_response=result.get("final_response"),
        requires_human_approval=bool(result.get("requires_human_approval", False)),
        errors=result.get("errors", []),
    )
