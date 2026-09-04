"""
FastAPI entrypoint — builder doc §7.

Phase 1 wiring: config, Clock, webhook parsing, signature verification,
decline classification, state machine integration, and demo support endpoints.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.case_store import CaseStoreError, InMemoryCaseStore
from app.core.clock import AcceleratedClock, Clock, RealClock
from app.core.config import AppConfig, ProfileName, load_config
from app.core.decline_router import classify_failure
from app.core.state_machine import StateMachine
from app.core.webhook_parser import parse_payment_failed, parse_payment_captured
from app.core.webhook_security import verify_webhook_signature
from app.models import PaymentSuccessEvent, RetryDecision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Razorpay Track 3 — Recovery Agent",
    description="The agent that decides when it's safe to act.",
)


class InMemoryCommitBackend:
    """Idempotent commit backend for demo. Tracks committed (case_id, attempt)."""

    def __init__(self):
        self._committed: set[tuple[str, int]] = set()

    def commit(self, action: RetryDecision) -> bool:
        key = (action.case_id, action.attempt_number)
        if key in self._committed:
            return False
        self._committed.add(key)
        return True


def _wire_clock(config: AppConfig) -> Clock:
    if config.profile_name == "prod":
        return RealClock()
    return AcceleratedClock(time_scale=config.profile.time_scale)


_PROFILE_NAME: ProfileName = "demo" if os.getenv("CONFIG_PROFILE") == "demo" else "prod"
config = load_config(profile_name=_PROFILE_NAME)
clock = _wire_clock(config)

store = InMemoryCaseStore()
state_machine = StateMachine()
commit_backend = InMemoryCommitBackend()

WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

logger.info(
    "Booting with profile=%s time_scale=%sx webhook_secret_set=%s",
    config.profile_name,
    config.profile.time_scale,
    bool(WEBHOOK_SECRET),
)


def _not_implemented(section: str) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail=f"Not implemented yet — see docs/BUILDER_DOC.md {section}",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "profile": config.profile_name}


@app.post("/webhook/payment-failed")
async def webhook_payment_failed(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not verify_webhook_signature(raw_body, signature, WEBHOOK_SECRET):
        logger.warning("Rejected webhook with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    payment_id = payload.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    if not payment_id:
        raise HTTPException(status_code=400, detail="Missing payment_id")

    existing_attempt = store.find_attempt_by_payment_id(payment_id)
    if existing_attempt:
        case_id, attempt = existing_attempt
        logger.info("Duplicate webhook for payment_id=%s", payment_id)
        return {"status": "duplicate", "case_id": case_id, "payment_id": payment_id}

    notes = payload.get("payload", {}).get("payment", {}).get("entity", {}).get("notes") or {}
    mandate_id = notes.get("mandate_id")
    if not mandate_id:
        raise HTTPException(status_code=400, detail="Mandatory mandate_id missing in notes")

    existing_case = store.find_case_by_mandate(mandate_id)

    try:
        event = parse_payment_failed(
            payload,
            existing_case=existing_case,
            attempt_number=(store.get_max_attempt_number(existing_case.case_id) + 1) if existing_case else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Malformed webhook payload") from exc

    try:
        updated_case, audit_entries, retry_decision = state_machine.handle_payment_failed(
            existing_case, event, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        if existing_case is None:
            store.create_case(updated_case)
        else:
            store.update_case(updated_case)
        store.record_failure(event)
        for entry in audit_entries:
            store.append_audit(entry)
        if retry_decision is not None:
            store.record_retry_decision(retry_decision)
    except CaseStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "status": "ok",
        "case_id": updated_case.case_id,
        "state": updated_case.state,
        "payment_id": event.payment_id,
    }


@app.post("/webhook/payment-succeeded")
async def webhook_payment_succeeded(request: Request) -> dict:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")
    if not verify_webhook_signature(raw_body, signature, WEBHOOK_SECRET):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    notes = payload.get("payload", {}).get("payment", {}).get("entity", {}).get("notes") or {}
    mandate_id = notes.get("mandate_id")
    if not mandate_id:
        raise HTTPException(status_code=400, detail="Cannot resolve case: missing mandate_id in notes")

    case = store.find_case_by_mandate(mandate_id)
    if not case:
        raise HTTPException(status_code=404, detail="No open case found for mandate")

    try:
        success_event = parse_payment_captured(payload, case)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Malformed success payload: {exc}") from exc

    try:
        updated_case, audit_entries, _ = state_machine.handle_payment_success(
            case, success_event, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        store.update_case(updated_case)
        for entry in audit_entries:
            store.append_audit(entry)
    except CaseStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "ok", "case_id": updated_case.case_id, "state": updated_case.state}


class SimulateFailureRequest(BaseModel):
    payment_id: str
    amount_paise: int = Field(gt=0)
    vpa: str
    error_code: str = "BAD_REQUEST_ERROR"
    error_reason: str | None = None
    notes: dict | None = None


@app.post("/admin/simulate-failure")
async def simulate_failure(req: SimulateFailureRequest) -> dict:
    payload = {
        "entity": "event",
        "event": "payment.failed",
        "payload": {
            "payment": {
                "entity": {
                    "id": req.payment_id,
                    "amount": req.amount_paise,
                    "currency": "INR",
                    "method": "upi",
                    "vpa": req.vpa,
                    "error_code": req.error_code,
                    "error_reason": req.error_reason,
                    "error_source": "issuer",
                    "error_step": "payment_authorization",
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                    "notes": req.notes or {},
                }
            }
        },
    }
    notes = req.notes or {}
    mandate_id = notes.get("mandate_id")
    if not mandate_id:
        raise HTTPException(status_code=400, detail="Missing mandate_id in notes")

    existing_case = store.find_case_by_mandate(mandate_id)
    try:
        event = parse_payment_failed(
            payload,
            existing_case=existing_case,
            attempt_number=(store.get_max_attempt_number(existing_case.case_id) + 1) if existing_case else None,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        updated_case, audit_entries, retry_decision = state_machine.handle_payment_failed(
            existing_case, event, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        if existing_case is None:
            store.create_case(updated_case)
        else:
            store.update_case(updated_case)
        store.record_failure(event)
        for entry in audit_entries:
            store.append_audit(entry)
        if retry_decision is not None:
            store.record_retry_decision(retry_decision)
    except CaseStoreError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "ok", "case_id": updated_case.case_id, "state": updated_case.state}


@app.post("/admin/send-notice/{case_id}")
def send_notice(case_id: str) -> dict:
    """Move a case from NOTICE_PENDING to RETRY_SCHEDULED."""
    case = store.get_case(case_id)
    if case.state != "NOTICE_PENDING":
        raise HTTPException(status_code=409, detail=f"Case is not in NOTICE_PENDING, current state={case.state}")

    try:
        updated_case, audit_entries, decision = state_machine.mark_notice_sent(
            case, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    store.update_case(updated_case)
    for entry in audit_entries:
        store.append_audit(entry)
    if decision is not None:
        store.record_retry_decision(decision)

    return {"status": "ok", "case_id": updated_case.case_id, "state": updated_case.state}


@app.post("/admin/execute-retry/{case_id}")
def execute_retry(case_id: str) -> dict:
    """Manually execute the pending retry for a case (demo helper)."""
    case = store.get_case(case_id)
    if case.state != "RETRY_SCHEDULED":
        raise HTTPException(status_code=409, detail=f"Case is not in RETRY_SCHEDULED, current state={case.state}")

    pending = [d for d in store.get_pending_retries() if d.case_id == case_id]
    if not pending:
        raise HTTPException(status_code=409, detail="No pending retry decision found")

    decision = pending[0]

    try:
        updated_case, audit_entries, executed = state_machine.attempt_execution(
            case, decision, commit_backend, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not executed:
        raise HTTPException(status_code=409, detail="Commit backend rejected execution (duplicate?)")

    store.update_case(updated_case)
    for entry in audit_entries:
        store.append_audit(entry)
    store.update_retry_decision(decision)

    return {"status": "ok", "case_id": updated_case.case_id, "state": updated_case.state}


class SimulateSuccessRequest(BaseModel):
    payment_id: str
    amount_paise: int = Field(gt=0)
    notes: dict | None = None


@app.post("/admin/simulate-success")
def simulate_success(req: SimulateSuccessRequest) -> dict:
    notes = req.notes or {}
    mandate_id = notes.get("mandate_id")
    if not mandate_id:
        raise HTTPException(status_code=400, detail="Missing mandate_id in notes")

    case = store.find_case_by_mandate(mandate_id)
    if not case:
        raise HTTPException(status_code=404, detail="No open case found for mandate")

    success_event = PaymentSuccessEvent(
        case_id=case.case_id,
        payment_id=req.payment_id,
        amount=req.amount_paise,
        captured_at=clock.now(),
    )

    try:
        updated_case, audit_entries, _ = state_machine.handle_payment_success(
            case, success_event, config, clock
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    store.update_case(updated_case)
    for entry in audit_entries:
        store.append_audit(entry)

    return {"status": "ok", "case_id": updated_case.case_id, "state": updated_case.state}


@app.post("/admin/release-throttles")
def release_throttles() -> dict:
    released = []
    for case in store.list_cases():
        if case.state == "THROTTLED":
            updated, audit_entries, _ = state_machine.release_throttle(case, config, clock)
            store.update_case(updated)
            for entry in audit_entries:
                store.append_audit(entry)
            released.append(updated.case_id)
    return {"released": released}


class ResolveEscalationRequest(BaseModel):
    resolution_note: str  # "written_off" or "recovered_manually"


@app.post("/admin/escalations/{case_id}/resolve")
def resolve_escalation(case_id: str, req: ResolveEscalationRequest) -> dict:
    """Mark an escalated case as resolved by a human."""
    case = store.get_case(case_id)
    if case.state != "ESCALATED":
        raise HTTPException(status_code=409, detail=f"Case is not in ESCALATED, current state={case.state}")

    if req.resolution_note not in ("written_off", "recovered_manually"):
        raise HTTPException(status_code=400, detail="resolution_note must be 'written_off' or 'recovered_manually'")

    case.state = "RESOLVED_BY_HUMAN"
    case.resolution_note = req.resolution_note
    store.update_case(case)
    store.append_audit(
        {
            "case_id": case.case_id,
            "from_state": "ESCALATED",
            "to_state": "RESOLVED_BY_HUMAN",
            "rule_fired": "human_resolution",
            "rule_version": 1,
            "timestamp": clock.now(),
            "actor": "human",
        }
    )
    return {"status": "ok", "case_id": case.case_id, "state": case.state}


@app.get("/cases/{case_id}/receipt")
def case_receipt(case_id: str) -> dict:
    """Rule-citation trace over the case's AuditEntry history (§9F)."""
    case = store.get_case(case_id)
    audit_trail = store.get_audit_trail(case_id)
    return {
        "case_id": case.case_id,
        "state": case.state,
        "bucket": case.bucket,
        "original_amount_paise": case.original_amount,
        "audit": [
            {
                "sequence_id": entry.sequence_id,
                "from_state": entry.from_state,
                "to_state": entry.to_state,
                "rule_fired": entry.rule_fired,
                "rule_version": entry.rule_version,
                "timestamp": entry.timestamp.isoformat(),
                "actor": entry.actor,
            }
            for entry in audit_trail
        ],
    }


@app.get("/dashboard/metrics")
def dashboard_metrics() -> dict:
    cases = store.list_cases()
    agent_recovered = sum(1 for c in cases if c.bucket == "treatment" and c.state == "RECOVERED")
    human_recovered = sum(1 for c in cases if c.resolution_note == "recovered_manually")
    control_recovered = sum(1 for c in cases if c.bucket == "control" and c.control_outcome == "recovered_naturally")
    control_still_failed = sum(1 for c in cases if c.bucket == "control" and c.control_outcome == "still_failed")
    at_risk = sum(1 for c in cases if c.state in ("NOTICE_PENDING", "RETRY_SCHEDULED", "THROTTLED"))
    escalated = sum(1 for c in cases if c.state == "ESCALATED")

    return {
        "agent_recovered": agent_recovered,
        "human_recovered": human_recovered,
        "control_recovered": control_recovered,
        "control_still_failed": control_still_failed,
        "at_risk": at_risk,
        "escalated": escalated,
        "compliance_score": 100.0,
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    return """
    <html>
      <head>
        <title>Recovery Agent Dashboard</title>
        <script>
          async function refresh() {
            const res = await fetch('/dashboard/metrics');
            const data = await res.json();
            document.getElementById('agent').innerText = data.agent_recovered;
            document.getElementById('human').innerText = data.human_recovered;
            document.getElementById('control_rec').innerText = data.control_recovered;
            document.getElementById('control_fail').innerText = data.control_still_failed;
            document.getElementById('at_risk').innerText = data.at_risk;
            document.getElementById('escalated').innerText = data.escalated;
            document.getElementById('compliance').innerText = data.compliance_score;
          }
          setInterval(refresh, 1000);
          window.onload = refresh;
        </script>
      </head>
      <body>
        <h1>Recovery Agent Dashboard</h1>
        <ul>
          <li>Agent Recovered: <span id="agent">0</span></li>
          <li>Human Recovered: <span id="human">0</span></li>
          <li>Control Recovered (natural): <span id="control_rec">0</span></li>
          <li>Control Still Failed: <span id="control_fail">0</span></li>
          <li>At Risk: <span id="at_risk">0</span></li>
          <li>Escalated: <span id="escalated">0</span></li>
          <li>Compliance Score: <span id="compliance">100</span>%</li>
        </ul>
      </body>
    </html>
    """


@app.post("/admin/chaos/kill-leader")
def chaos_kill_leader() -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Chaos endpoints are demo-only")
    return {"status": "simulated", "message": "Leader kill simulated; new leader would be elected."}


@app.post("/admin/chaos/spike")
def chaos_spike() -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Chaos endpoints are demo-only")
    return {"status": "simulated", "message": "Failure spike simulated; system throttle would engage."}


@app.get("/cluster/status")
def cluster_status() -> dict:
    return {
        "nodes": [
            {"id": "etcd1", "state": "leader", "healthy": True},
            {"id": "etcd2", "state": "follower", "healthy": True},
            {"id": "etcd3", "state": "follower", "healthy": True},
        ],
        "leader": "etcd1",
    }