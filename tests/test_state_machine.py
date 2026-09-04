"""Unit tests for app/core/state_machine.py (builder doc §5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.clock import RealClock
from app.core.config import AppConfig, load_config
from app.core.state_machine import StateMachine, is_control_case
from app.models import (
    PaymentFailureEvent,
    PaymentSuccessEvent,
    RecoveryCase,
    assign_bucket,
)

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def config() -> AppConfig:
    return load_config(profile_name="prod")


@pytest.fixture
def clock() -> RealClock:
    return RealClock()


@pytest.fixture
def sm() -> StateMachine:
    return StateMachine()


def make_failure(
    case_id: str = "pay_abc123",
    payment_id: str | None = None,
    attempt_number: int = 1,
    amount: int = 10000,  # ₹100.00 in paise
    reason_code: str = "bank_server_down",
    error_reason: str | None = None,
    mandate_id: str = "mandate_1",
    instrument_id: str = "hashed_instr_1",
) -> PaymentFailureEvent:
    return PaymentFailureEvent(
        case_id=case_id,
        payment_id=payment_id or f"pay_{case_id}_{attempt_number}",
        attempt_number=attempt_number,
        reason_code=reason_code,
        decline_class="unclassified",
        amount=amount,
        received_at=datetime.now(timezone.utc),
        mandate_id=mandate_id,
        instrument_id=instrument_id,
        error_reason=error_reason,
    )


def make_case(
    case_id: str = "pay_abc123",
    mandate_id: str = "mandate_1",
    instrument_id: str = "hashed_instr_1",
    original_amount: int = 10000,
    state: str = "RECEIVED",
    bucket: str | None = None,
) -> RecoveryCase:
    case = RecoveryCase(
        case_id=case_id,
        mandate_id=mandate_id,
        instrument_id=instrument_id,
        original_amount=original_amount,
        opened_at=datetime.now(timezone.utc),
        state=state,
    )
    if bucket:
        assign_bucket(case, bucket)  # type: ignore[arg-type]
    return case


def make_success(case_id: str, amount: int, payment_id: str | None = None) -> PaymentSuccessEvent:
    return PaymentSuccessEvent(
        case_id=case_id,
        payment_id=payment_id or f"pay_success_{case_id}",
        amount=amount,
        captured_at=datetime.now(timezone.utc),
    )


class TestNewFailure:
    def test_technical_failure_leads_to_notice_pending(self, sm, config, clock):
        event = make_failure(reason_code="bank_server_down")
        case, audit, decision = sm.handle_payment_failed(None, event, config, clock)
        assert case.state == "NOTICE_PENDING"
        assert case.bucket == "treatment"
        assert decision is None

    def test_business_failure_escalates_immediately(self, sm, config, clock):
        event = make_failure(reason_code="insufficient_funds")
        case, audit, decision = sm.handle_payment_failed(None, event, config, clock)
        assert case.state == "ESCALATED"
        assert case.bucket == "treatment"

    def test_high_amount_technical_failure_routes_to_afa_required(self, sm, config, clock):
        event = make_failure(amount=2000000, reason_code="bank_server_down")  # ₹20,000 > ceiling
        case, audit, decision = sm.handle_payment_failed(None, event, config, clock)
        assert case.state == "AFA_REQUIRED"

    def test_control_case_is_held(self, sm, config, clock):
        # Find a control case_id deterministically
        control_id = None
        for i in range(1000):
            candidate = f"pay_control_{i}"
            if is_control_case(candidate):
                control_id = candidate
                break
        assert control_id is not None
        event = make_failure(case_id=control_id, payment_id=control_id)
        case, audit, decision = sm.handle_payment_failed(None, event, config, clock)
        assert case.state == "CONTROL_HELD"
        assert case.bucket == "control"
        assert case.control_observation_deadline is not None


class TestExistingFailure:
    def test_retry_executed_failure_increments_retries_and_goes_to_notice_pending(self, sm, config, clock):
        case = make_case(state="RETRY_EXECUTED", bucket="treatment")
        event = make_failure(payment_id="pay_retry_2", attempt_number=2, reason_code="bank_server_down")
        updated, audit, decision = sm.handle_payment_failed(case, event, config, clock)
        assert updated.retries_used == 1
        assert updated.state == "NOTICE_PENDING"

    def test_retry_executed_business_failure_escalates(self, sm, config, clock):
        case = make_case(state="RETRY_EXECUTED", bucket="treatment")
        event = make_failure(payment_id="pay_retry_2", attempt_number=2, reason_code="insufficient_funds")
        updated, audit, decision = sm.handle_payment_failed(case, event, config, clock)
        assert updated.state == "ESCALATED"

    def test_retry_cap_reached_escalates(self, sm, config, clock):
        case = make_case(state="RETRY_EXECUTED", bucket="treatment")
        case.retries_used = config.npci_rules.max_retries - 1  # one more retry will hit cap
        event = make_failure(payment_id="pay_retry_3", attempt_number=3, reason_code="bank_server_down")
        updated, audit, decision = sm.handle_payment_failed(case, event, config, clock)
        assert updated.retries_used == config.npci_rules.max_retries
        assert updated.state == "ESCALATED"

    def test_control_held_failure_signal_closes_case(self, sm, config, clock):
        case = make_case(state="CONTROL_HELD", bucket="control")
        event = make_failure(payment_id="pay_retry_2", attempt_number=2)
        updated, audit, decision = sm.handle_payment_failed(case, event, config, clock)
        assert updated.state == "CONTROL_STILL_FAILED"
        assert updated.control_outcome == "still_failed"


class TestSuccessHandling:
    def test_agent_recovered_from_retry_executed(self, sm, config, clock):
        case = make_case(state="RETRY_EXECUTED", bucket="treatment")
        success = make_success(case.case_id, case.original_amount)
        updated, audit, decision = sm.handle_payment_success(case, success, config, clock)
        assert updated.state == "RECOVERED"

    def test_natural_recovery_before_execution(self, sm, config, clock):
        case = make_case(state="NOTICE_PENDING", bucket="treatment")
        success = make_success(case.case_id, case.original_amount)
        updated, audit, decision = sm.handle_payment_success(case, success, config, clock)
        assert updated.state == "RECOVERED_NATURALLY"

    def test_control_recovery_sets_control_recovered(self, sm, config, clock):
        case = make_case(state="CONTROL_HELD", bucket="control")
        success = make_success(case.case_id, case.original_amount)
        updated, audit, decision = sm.handle_payment_success(case, success, config, clock)
        assert updated.state == "CONTROL_RECOVERED"
        assert updated.control_outcome == "recovered_naturally"


class TestNoticeScheduling:
    def test_mark_notice_sent_generates_retry_decision(self, sm, config, clock):
        case = make_case(state="NOTICE_PENDING", bucket="treatment")
        updated, audit, decision = sm.mark_notice_sent(case, config, clock, reason_code="bank_server_down")
        assert updated.state == "RETRY_SCHEDULED"
        assert decision is not None
        assert decision.attempt_number == 1
        assert decision.scheduled_at is not None

    def test_salary_window_bias_for_insufficient_funds(self, sm, config, clock):
        # Directly test the private _compute_scheduled_at since insufficient_funds
        # is business in our router and wouldn't normally reach NOTICE_PENDING.
        scheduled_at, reasoning = sm._compute_scheduled_at(
            retries_used=0,
            reason_code="insufficient_funds",
            config=config,
            clock=clock,
            previous_executed_at=None,
        )
        ist_time = scheduled_at.astimezone(IST)
        assert 1 <= ist_time.day <= 7
        assert "insufficient_funds bias applied" in reasoning

    def test_peak_hour_blackout_pushes_schedule(self, sm, config, clock):
        # Simulate a time inside a peak window
        now = datetime(2025, 1, 15, 10, 30, tzinfo=IST)
        scheduled = now + timedelta(hours=1)  # 11:30 IST, inside peak
        new_scheduled = sm._avoid_peak_windows(scheduled, config, clock, now)
        new_ist = new_scheduled.astimezone(IST)
        assert not (10 <= new_ist.hour < 13)  # not inside 10-13 peak
        assert new_ist.hour == 13 and new_ist.minute == 0


class TestThrottle:
    def test_throttle_does_not_change_retries_or_attempt(self, sm, config, clock):
        case = make_case(state="RETRY_SCHEDULED", bucket="treatment")
        case.retries_used = 1
        updated, audit, _ = sm.apply_throttle(case, config, clock)
        assert updated.state == "THROTTLED"
        assert updated.retries_used == 1
        updated2, audit2, _ = sm.release_throttle(updated, config, clock)
        assert updated2.state == "RETRY_SCHEDULED"
        assert updated2.retries_used == 1