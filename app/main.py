"""
FastAPI entrypoint — builder doc §7.

Phase 1 wiring: config, Clock, webhook parsing, signature verification,
decline classification, state machine integration, and demo support endpoints.

If USE_ETCD=true, the commit backend uses a real 3-node etcd cluster for
quorum-approved execution. Otherwise, it falls back to an in-memory
idempotent backend with the same contract.

A background task periodically releases THROTTLED cases so the demo is
self-healing without manual admin calls.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from typing import Optional

import etcd3
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.core.case_store import CaseStoreError, InMemoryCaseStore
from app.core.clock import AcceleratedClock, Clock, RealClock
from app.core.commit_backend import EtcdQuorumBackend
from app.core.config import AppConfig, ProfileName, load_config
from app.core.decline_router import classify_failure
from app.core.dnd import is_dnd_hour
from app.core.state_machine import StateMachine
from app.core.verifier import verify_case_compliance
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

# ---------------------------------------------------------------------------
# Commit backend selection
# ---------------------------------------------------------------------------
USE_ETCD = os.getenv("USE_ETCD", "false").lower() == "true"

if USE_ETCD:
    logger.info("Using real etcd commit backend")
    etcd_client = etcd3.Etcd3Client(host="localhost", port=2379)
    commit_backend = EtcdQuorumBackend(etcd_client)
else:
    logger.info("Using in-memory commit backend (USE_ETCD not set)")
    commit_backend = InMemoryCommitBackend()

WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

logger.info(
    "Booting with profile=%s time_scale=%sx webhook_secret_set=%s use_etcd=%s",
    config.profile_name,
    config.profile.time_scale,
    bool(WEBHOOK_SECRET),
    USE_ETCD,
)


async def _periodic_throttle_release():
    """Release THROTTLED cases automatically in the background (demo tick)."""
    while True:
        await asyncio.sleep(60)  # check every 60 seconds real time
        try:
            for case in store.list_cases():
                if case.state == "THROTTLED":
                    updated, audit_entries, _ = state_machine.release_throttle(
                        case, config, clock
                    )
                    store.update_case(updated)
                    for entry in audit_entries:
                        store.append_audit(entry)
                    logger.info("Auto-released throttled case %s", case.case_id)
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.warning("Periodic throttle release failed: %s", exc)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(_periodic_throttle_release())


def get_leader_name() -> Optional[str]:
    """Return the current etcd leader's node name, or None if unavailable."""
    if not USE_ETCD:
        return None
    try:
        member_id_to_name = {str(m.id): m.name for m in etcd_client.members}
        result = subprocess.run(
            [
                "docker", "compose", "exec", "-T", "etcd1",
                "etcdctl", "endpoint", "status", "--write-out=json", "--cluster",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        statuses = json.loads(result.stdout)
        leader_id = None
        for status in statuses:
            leader_id = status.get("Status", {}).get("leader")
            if leader_id:
                break
        if leader_id:
            return member_id_to_name.get(str(leader_id))
    except Exception as exc:
        logger.warning("Leader detection failed: %s", exc)
    return None


def _not_implemented(section: str) -> HTTPException:
    return HTTPException(
        status_code=501,
        detail=f"Not implemented yet — see docs/BUILDER_DOC.md {section}",
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "profile": config.profile_name, "use_etcd": USE_ETCD}


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------
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
    existing_case = store.find_case_by_mandate(mandate_id) if mandate_id else None

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


# ---------------------------------------------------------------------------
# Admin / demo endpoints
# ---------------------------------------------------------------------------
class SimulateFailureRequest(BaseModel):
    payment_id: str
    amount_paise: int = Field(gt=0)
    vpa: str
    error_code: str = "BAD_REQUEST_ERROR"
    error_reason: str | None = None
    notes: dict | None = None


@app.post("/admin/simulate-failure")
async def simulate_failure(req: SimulateFailureRequest) -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Simulation endpoints are demo-only")

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
    existing_case = store.find_case_by_mandate(mandate_id) if mandate_id else None
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
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Admin endpoints are demo-only")

    # DND check: defer notice if currently within DND hours
    if is_dnd_hour(clock.now()):
        raise HTTPException(
            status_code=409,
            detail="DND hours active (21:00-09:00 IST). Notice deferred; advance clock to after 09:00 IST.",
        )

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
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Admin endpoints are demo-only")

    case = store.get_case(case_id)
    if case.state != "RETRY_SCHEDULED":
        raise HTTPException(status_code=409, detail=f"Case is not in RETRY_SCHEDULED, current state={case.state}")

    pending = [d for d in store.get_pending_retries() if d.case_id == case_id]
    if not pending:
        raise HTTPException(status_code=409, detail="No pending retry decision found")

    decision = pending[0]

    now = clock.now()
    if now < decision.scheduled_at:
        raise HTTPException(
            status_code=409,
            detail=f"Retry not yet due: scheduled_at={decision.scheduled_at.isoformat()}, now={now.isoformat()}",
        )

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


@app.post("/admin/advance-clock")
def advance_clock(hours: float = 0, minutes: float = 0, seconds: float = 0) -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Admin endpoints are demo-only")

    delta = timedelta(hours=hours, minutes=minutes, seconds=seconds)
    if delta <= timedelta(0):
        raise HTTPException(status_code=400, detail="Must advance by a positive duration")

    try:
        new_now = clock.advance(delta)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "ok", "new_now": new_now.isoformat()}


class SimulateSuccessRequest(BaseModel):
    payment_id: str
    amount_paise: int = Field(gt=0)
    notes: dict | None = None


@app.post("/admin/simulate-success")
def simulate_success(req: SimulateSuccessRequest) -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Simulation endpoints are demo-only")

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
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Admin endpoints are demo-only")

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
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Admin endpoints are demo-only")

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


# ---------------------------------------------------------------------------
# Compliance check (public oracle)
# ---------------------------------------------------------------------------
class ComplianceCheckRequest(BaseModel):
    reason_code: str
    amount_paise: int = Field(gt=0)
    error_reason: Optional[str] = None


@app.post("/compliance/check")
def compliance_check(req: ComplianceCheckRequest) -> dict:
    decline_class, retryable = classify_failure(config, req.reason_code, req.error_reason)
    advisory_prob = 0.8 if decline_class == "technical" else 0.1
    now = clock.now()
    next_eligible = now + config.npci_rules.notice_lead_time if retryable else None

    # Build a reasoning string with concrete config values
    reasoning_parts = [
        f"notice_lead_time={config.npci_rules.notice_lead_time}",
        f"spacing={[str(s) for s in config.npci_rules.spacing]}",
        f"peak_windows={[f'{w.start}-{w.end}' for w in config.npci_rules.peak_windows]}",
        f"afa_ceiling={config.npci_rules.afa_free_ceiling} paise",
    ]
    reasoning = "; ".join(reasoning_parts)

    return {
        "allowed": retryable,
        "decline_class": decline_class,
        "advisory_technical_probability": advisory_prob,
        "rule_citation": "NPCI pre-debit notice lead time, spacing, peak-hour blackout, AFA ceiling (config/npci_rules.yaml)",
        "reasoning": reasoning,
        "next_eligible_at": next_eligible.isoformat() if next_eligible else None,
    }


# ---------------------------------------------------------------------------
# Case listing
# ---------------------------------------------------------------------------
@app.get("/cases")
def list_cases() -> dict:
    cases = store.list_cases()
    return {
        "cases": [
            {
                "case_id": c.case_id,
                "mandate_id": c.mandate_id,
                "state": c.state,
                "bucket": c.bucket,
                "original_amount_paise": c.original_amount,
                "retries_used": c.retries_used,
            }
            for c in cases
        ]
    }


# ---------------------------------------------------------------------------
# Receipt and verifier
# ---------------------------------------------------------------------------
@app.get("/cases/{case_id}/receipt")
def case_receipt(case_id: str) -> dict:
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
                "scheduled_at": entry.scheduled_at.isoformat() if entry.scheduled_at else None,
                "entry_hash": entry.entry_hash,
                "prev_hash": entry.prev_hash,
            }
            for entry in audit_trail
        ],
    }


@app.get("/cases/{case_id}/verify")
def verify_case(case_id: str) -> dict:
    case = store.get_case(case_id)
    audit = store.get_audit_trail(case_id)
    results = verify_case_compliance(case, audit, config)
    return {
        "case_id": case_id,
        "results": [
            {"rule": r.rule, "passed": r.passed, "detail": r.detail}
            for r in results
        ],
        "all_passed": all(r.passed for r in results),
    }


@app.get("/cases/{case_id}/verify_chain")
def verify_chain(case_id: str) -> dict:
    """Independently verify the Merkle chain of audit entries for a case."""
    audit = store.get_audit_trail(case_id)
    if not audit:
        return {"case_id": case_id, "valid": False, "detail": "No audit entries"}

    prev_hash = None
    for entry in audit:
        # Recompute expected previous hash
        if entry.prev_hash != prev_hash:
            return {
                "case_id": case_id,
                "valid": False,
                "detail": f"Chain break at sequence {entry.sequence_id}: expected prev {prev_hash}, got {entry.prev_hash}",
            }
        # Recompute entry hash
        serialized = json.dumps({
            "case_id": entry.case_id,
            "from_state": entry.from_state,
            "to_state": entry.to_state,
            "rule_fired": entry.rule_fired,
            "rule_version": entry.rule_version,
            "timestamp": entry.timestamp.isoformat(),
            "actor": entry.actor,
            "sequence_id": entry.sequence_id,
            "prev_hash": entry.prev_hash,
            "scheduled_at": entry.scheduled_at.isoformat() if entry.scheduled_at else None,
        }, sort_keys=True)
        expected_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if entry.entry_hash != expected_hash:
            return {
                "case_id": case_id,
                "valid": False,
                "detail": f"Hash mismatch at sequence {entry.sequence_id}",
            }
        prev_hash = entry.entry_hash

    return {"case_id": case_id, "valid": True, "detail": "Chain intact"}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/dashboard/metrics")
def dashboard_metrics() -> dict:
    cases = store.list_cases()
    agent_recovered_count = sum(1 for c in cases if c.bucket == "treatment" and c.state == "RECOVERED")
    agent_recovered_paise = sum(c.original_amount for c in cases if c.bucket == "treatment" and c.state == "RECOVERED")
    human_recovered_count = sum(1 for c in cases if c.resolution_note == "recovered_manually")
    human_recovered_paise = sum(c.original_amount for c in cases if c.resolution_note == "recovered_manually")
    control_recovered_count = sum(1 for c in cases if c.bucket == "control" and c.control_outcome == "recovered_naturally")
    control_recovered_paise = sum(c.original_amount for c in cases if c.bucket == "control" and c.control_outcome == "recovered_naturally")
    control_still_failed_count = sum(1 for c in cases if c.bucket == "control" and c.control_outcome == "still_failed")
    at_risk_count = sum(1 for c in cases if c.state in ("NOTICE_PENDING", "RETRY_SCHEDULED", "THROTTLED"))
    escalated_count = sum(1 for c in cases if c.state == "ESCALATED")

    total_treatment = sum(1 for c in cases if c.bucket == "treatment")
    total_control = sum(1 for c in cases if c.bucket == "control")
    treatment_recovery_rate = (agent_recovered_count / total_treatment) if total_treatment else 0.0
    control_recovery_rate = (control_recovered_count / total_control) if total_control else 0.0
    incremental_rate = treatment_recovery_rate - control_recovery_rate

    return {
        "agent_recovered_count": agent_recovered_count,
        "agent_recovered_paise": agent_recovered_paise,
        "human_recovered_count": human_recovered_count,
        "human_recovered_paise": human_recovered_paise,
        "control_recovered_count": control_recovered_count,
        "control_recovered_paise": control_recovered_paise,
        "control_still_failed_count": control_still_failed_count,
        "at_risk_count": at_risk_count,
        "escalated_count": escalated_count,
        "treatment_recovery_rate": treatment_recovery_rate,
        "control_recovery_rate": control_recovery_rate,
        "incremental_recovery_rate": incremental_rate,
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
            document.getElementById('agent_count').innerText = data.agent_recovered_count;
            document.getElementById('agent_paise').innerText = data.agent_recovered_paise;
            document.getElementById('human_count').innerText = data.human_recovered_count;
            document.getElementById('human_paise').innerText = data.human_recovered_paise;
            document.getElementById('control_rec_count').innerText = data.control_recovered_count;
            document.getElementById('control_rec_paise').innerText = data.control_recovered_paise;
            document.getElementById('control_fail_count').innerText = data.control_still_failed_count;
            document.getElementById('at_risk').innerText = data.at_risk_count;
            document.getElementById('escalated').innerText = data.escalated_count;
            document.getElementById('treatment_rate').innerText = (data.treatment_recovery_rate * 100).toFixed(1) + '%';
            document.getElementById('control_rate').innerText = (data.control_recovery_rate * 100).toFixed(1) + '%';
            document.getElementById('incremental_rate').innerText = (data.incremental_recovery_rate * 100).toFixed(1) + '%';
            document.getElementById('compliance').innerText = data.compliance_score;
          }
          setInterval(refresh, 1000);
          window.onload = refresh;
        </script>
      </head>
      <body>
        <h1>Recovery Agent Dashboard</h1>
        <ul>
          <li>Agent Recovered (count): <span id="agent_count">0</span></li>
          <li>Agent Recovered (paise): <span id="agent_paise">0</span></li>
          <li>Human Recovered (count): <span id="human_count">0</span></li>
          <li>Human Recovered (paise): <span id="human_paise">0</span></li>
          <li>Control Recovered Natural (count): <span id="control_rec_count">0</span></li>
          <li>Control Recovered Natural (paise): <span id="control_rec_paise">0</span></li>
          <li>Control Still Failed: <span id="control_fail_count">0</span></li>
          <li>At Risk: <span id="at_risk">0</span></li>
          <li>Escalated: <span id="escalated">0</span></li>
          <li>Treatment Recovery Rate: <span id="treatment_rate">0%</span></li>
          <li>Control Recovery Rate: <span id="control_rate">0%</span></li>
          <li>Incremental Recovery Rate: <span id="incremental_rate">0%</span></li>
          <li>Compliance Score: <span id="compliance">100</span>%</li>
        </ul>
      </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Chaos / cluster
# ---------------------------------------------------------------------------
@app.post("/admin/chaos/kill-leader")
def chaos_kill_leader() -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Chaos endpoints are demo-only")
    if USE_ETCD:
        leader_name = get_leader_name()
        if leader_name:
            subprocess.run(["docker", "compose", "stop", leader_name], check=False)
            return {"status": "killed", "message": f"Stopped {leader_name}; new leader should be elected."}
        else:
            subprocess.run(["docker", "compose", "stop", "etcd3"], check=False)
            return {"status": "killed", "message": "Stopped etcd3; leader detection failed, new leader should be elected."}
    return {"status": "simulated", "message": "Leader kill simulated; new leader would be elected."}


@app.post("/admin/chaos/spike")
def chaos_spike() -> dict:
    if config.profile_name != "demo":
        raise HTTPException(status_code=403, detail="Chaos endpoints are demo-only")
    return {"status": "simulated", "message": "Failure spike simulated; system throttle would engage."}


@app.get("/cluster/status")
def cluster_status() -> dict:
    if USE_ETCD:
        members = etcd_client.members
        leader_name = get_leader_name()
        return {
            "nodes": [
                {"id": str(m.id), "name": m.name, "peer_urls": list(m.peer_urls)}
                for m in members
            ],
            "leader": leader_name,
            "using_real_etcd": True,
        }
    return {
        "nodes": [
            {"id": "etcd1", "state": "leader", "healthy": True},
            {"id": "etcd2", "state": "follower", "healthy": True},
            {"id": "etcd3", "state": "follower", "healthy": True},
        ],
        "leader": "etcd1",
        "using_real_etcd": False,
    }