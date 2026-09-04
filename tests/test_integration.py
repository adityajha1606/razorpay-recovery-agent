"""End-to-end integration test using a manual test clock."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.clock import Clock
from app.core.case_store import InMemoryCaseStore
from app.core.config import load_config
from app.core.state_machine import StateMachine
from app.core.webhook_parser import parse_payment_failed
from app.models import PaymentSuccessEvent


class ManualClock(Clock):
    """Manual clock for integration tests — allows instant time advancement."""

    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2025, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        # We bypass scaling in integration tests; use real delays for simplicity.
        return real_delay

    def advance(self, delta: timedelta) -> None:
        self.current += delta


def test_full_recovery_cycle(monkeypatch):
    # Force treatment bucket for this test — we are testing the agent action path.
    monkeypatch.setattr("app.core.state_machine.is_control_case", lambda case_id: False)

    config = load_config(profile_name="prod")
    sm = StateMachine()
    store = InMemoryCaseStore()
    clock = ManualClock()

    # 1. Simulate a webhook payment.failed for a technical decline
    payload = {
        "payload": {"payment": {"entity": {
            "id": "pay_test_integration",
            "amount": 50000,  # ₹500 in paise
            "currency": "INR",
            "method": "upi",
            "vpa": "customer@upi",
            "error_code": "bank_server_down",
            "error_reason": "bank_down",
            "error_source": "issuer",
            "error_step": "payment_authorization",
            "created_at": int(clock.now().timestamp()),
            "notes": {"mandate_id": "mandate_integration"},
        }}}
    }
    event = parse_payment_failed(payload)
    case, audit, decision = sm.handle_payment_failed(None, event, config, clock)
    store.create_case(case)
    store.record_failure(event)
    for entry in audit:
        store.append_audit(entry)

    assert case.state == "NOTICE_PENDING"
    assert case.bucket == "treatment"

    # 2. Notice sent → retry scheduled
    case, audit, decision = sm.mark_notice_sent(case, config, clock, reason_code=event.reason_code)
    store.update_case(case)
    for entry in audit:
        store.append_audit(entry)
    store.record_retry_decision(decision)

    assert case.state == "RETRY_SCHEDULED"
    assert decision is not None
    assert decision.scheduled_at > clock.now()  # scheduled in the future

    # 3. Advance clock to scheduled time and execute
    clock.advance(decision.scheduled_at - clock.now())
    case, audit, _ = sm.mark_retry_executed(case, decision, config, clock)
    store.update_case(case)
    for entry in audit:
        store.append_audit(entry)

    assert case.state == "RETRY_EXECUTED"

    # 4. Simulate successful payment
    success = PaymentSuccessEvent(
        case_id=case.case_id,
        payment_id="pay_success_integration",
        amount=case.original_amount,
        captured_at=clock.now(),
    )
    case, audit, _ = sm.handle_payment_success(case, success, config, clock)
    store.update_case(case)
    for entry in audit:
        store.append_audit(entry)

    assert case.state == "RECOVERED"

    # 5. Verify audit trail is non-empty and contains key transitions
    trail = store.get_audit_trail(case.case_id)
    states_seen = [entry.to_state for entry in trail]
    assert "NOTICE_PENDING" in states_seen
    assert "RETRY_SCHEDULED" in states_seen
    assert "RETRY_EXECUTED" in states_seen
    assert "RECOVERED" in states_seen