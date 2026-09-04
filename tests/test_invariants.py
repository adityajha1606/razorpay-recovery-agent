"""
The 8 Hypothesis property-test invariants — builder doc §8.

Implemented as real Hypothesis tests against the deterministic state machine,
in-memory store, and minimal support services (throttle, commit mock).
Each test generates random inputs and asserts the invariant holds.

All tests run against the `prod` config profile, never demo (CLAUDE.md rule 3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

import pytest
from hypothesis import given, settings, strategies as st

from app.core.case_store import InMemoryCaseStore
from app.core.config import load_config
from app.core.state_machine import StateMachine
from app.core.throttle import InstrumentThrottle
from app.models import (
    PaymentFailureEvent,
    PaymentSuccessEvent,
    RecoveryCase,
    RetryDecision,
    assign_bucket,
)

pytestmark = pytest.mark.invariants


class FixedClock:
    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2025, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        return real_delay

    def advance(self, delta: timedelta):
        self.current += delta


@runtime_checkable
class CommitBackend(Protocol):
    def commit(self, action: RetryDecision) -> bool: ...


class AlwaysTrueCommit:
    def commit(self, action: RetryDecision) -> bool:
        return True


class AlwaysFalseCommit:
    def commit(self, action: RetryDecision) -> bool:
        return False


def make_case(case_id: str, amount: int, bucket=None) -> RecoveryCase:
    case = RecoveryCase(
        case_id=case_id,
        mandate_id=f"mandate_{case_id}",
        instrument_id=f"instr_{case_id}",
        original_amount=amount,
        opened_at=datetime.now(timezone.utc),
        state="RECEIVED",
    )
    if bucket:
        assign_bucket(case, bucket)
    return case


def make_failure(
    case_id: str,
    attempt: int,
    reason: str = "bank_server_down",
    amount: int = 10000,
) -> PaymentFailureEvent:
    return PaymentFailureEvent(
        case_id=case_id,
        payment_id=f"pay_{case_id}_{attempt}",
        attempt_number=attempt,
        reason_code=reason,
        decline_class="unclassified",
        amount=amount,
        received_at=datetime.now(timezone.utc),
        mandate_id=f"mandate_{case_id}",
        instrument_id=f"instr_{case_id}",
    )


def make_success(case_id: str, amount: int) -> PaymentSuccessEvent:
    return PaymentSuccessEvent(
        case_id=case_id,
        payment_id=f"pay_success_{case_id}",
        amount=amount,
        captured_at=datetime.now(timezone.utc),
    )


def _sm():
    return StateMachine()

def _config():
    return load_config(profile_name="prod")


# ---------------------------------------------------------------------------
# Invariant 7: bucket immutability
# ---------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=1000000),
)
def test_invariant_7_bucket_immutability(case_id, amount):
    case = make_case(case_id, amount)
    assign_bucket(case, "treatment")
    assign_bucket(case, "treatment")
    assert case.bucket == "treatment"

    with pytest.raises(ValueError):
        assign_bucket(case, "control")
    assert case.bucket == "treatment"


# ---------------------------------------------------------------------------
# Invariant 8: throttle budget neutrality
# ---------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=1500000),
    reason=st.sampled_from(["bank_server_down", "insufficient_funds", "network_timeout"]),
)
def test_invariant_8_throttle_budget_neutrality(case_id, amount, reason):
    sm = _sm()
    config = _config()
    case = make_case(case_id, amount)
    case.bucket = "treatment"
    case.state = "RETRY_SCHEDULED"
    initial_retries = case.retries_used
    clock = FixedClock()

    for _ in range(5):
        updated, _, _ = sm.apply_throttle(case, config, clock)
        assert updated.state == "THROTTLED"
        updated, _, _ = sm.release_throttle(updated, config, clock)
        assert updated.state == "RETRY_SCHEDULED"

    assert case.retries_used == initial_retries


# ---------------------------------------------------------------------------
# Invariant 1: no case exceeds retry cap
# ---------------------------------------------------------------------------
@settings(max_examples=50)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=100000),
)
def test_invariant_1_no_case_exceeds_retry_cap(case_id, amount):
    sm = _sm()
    config = _config()
    clock = FixedClock()
    event = make_failure(case_id, 1, "bank_server_down", amount)
    case, _, _ = sm.handle_payment_failed(None, event, config, clock)

    attempt = 1
    while case.state != "ESCALATED" and attempt <= config.npci_rules.max_retries + 2:
        if case.state == "NOTICE_PENDING":
            case, _, decision = sm.mark_notice_sent(case, config, clock)
            case, _, _ = sm.mark_retry_executed(case, decision, config, clock)
            event = make_failure(case_id, attempt + 1, "bank_server_down", amount)
            case, _, _ = sm.handle_payment_failed(case, event, config, clock)
            attempt += 1
        else:
            break

    assert case.retries_used <= config.npci_rules.max_retries


# ---------------------------------------------------------------------------
# Invariant 5: monetary reconciliation exact
# ---------------------------------------------------------------------------
@settings(max_examples=50)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=100000),
)
def test_invariant_5_monetary_reconciliation_exact(case_id, amount):
    sm = _sm()
    config = _config()
    case = make_case(case_id, amount)
    case.bucket = "treatment"
    case.state = "RETRY_EXECUTED"
    clock = FixedClock()

    success = make_success(case_id, amount)
    updated, _, _ = sm.handle_payment_success(case, success, config, clock)
    assert updated.state == "RECOVERED"
    assert updated.original_amount == amount


# ---------------------------------------------------------------------------
# Invariant 4: no duplicate attempt execution
# ---------------------------------------------------------------------------
@settings(max_examples=20)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=100000),
)
def test_invariant_4_no_duplicate_attempt_execution(case_id, amount):
    sm = _sm()
    config = _config()
    case = make_case(case_id, amount)
    case.bucket = "treatment"
    case.state = "NOTICE_PENDING"
    clock = FixedClock()

    updated, _, decision = sm.mark_notice_sent(case, config, clock)
    assert decision is not None
    assert updated.state == "RETRY_SCHEDULED"

    updated, _, _ = sm.mark_retry_executed(updated, decision, config, clock)
    assert updated.state == "RETRY_EXECUTED"

    with pytest.raises(ValueError):
        sm.mark_retry_executed(updated, decision, config, clock)


# ---------------------------------------------------------------------------
# Invariant 2: retry spacing by executed_at
# ---------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    prev_executed_at=st.datetimes(timezones=st.just(timezone.utc)),
    retries_used=st.integers(min_value=0, max_value=2),
)
def test_invariant_2_retry_spacing_respects_executed_at(prev_executed_at, retries_used):
    sm = _sm()
    config = _config()
    clock = FixedClock(start=prev_executed_at + timedelta(minutes=1))

    scheduled_at, _ = sm._compute_scheduled_at(
        retries_used=retries_used,
        reason_code="bank_server_down",
        config=config,
        clock=clock,
        previous_executed_at=prev_executed_at,
    )

    required_spacing = config.npci_rules.spacing[min(retries_used, len(config.npci_rules.spacing)-1)]
    assert scheduled_at >= prev_executed_at + required_spacing


# ---------------------------------------------------------------------------
# Invariant 3: no execution without commit approval
# ---------------------------------------------------------------------------
@settings(max_examples=20)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=100000),
    commit_success=st.booleans(),
)
def test_invariant_3_no_execution_without_commit_approval(case_id, amount, commit_success):
    sm = _sm()
    config = _config()
    clock = FixedClock()
    case = make_case(case_id, amount)
    case.bucket = "treatment"
    case.state = "NOTICE_PENDING"

    updated, _, decision = sm.mark_notice_sent(case, config, clock)
    assert decision is not None

    commit_backend = AlwaysTrueCommit() if commit_success else AlwaysFalseCommit()

    updated, _, executed = sm.attempt_execution(
        updated, decision, commit_backend, config, clock
    )

    if commit_success:
        assert executed is True
        assert updated.state == "RETRY_EXECUTED"
    else:
        assert executed is False
        assert updated.state == "RETRY_SCHEDULED"


# ---------------------------------------------------------------------------
# Invariant 6: fraud throttle spacing
# ---------------------------------------------------------------------------
@settings(max_examples=100)
@given(
    instrument_id=st.text(min_size=1, max_size=20),
    first_action_utc=st.datetimes(timezones=st.just(timezone.utc)),
    second_action_delay_minutes=st.integers(min_value=0, max_value=120),
)
def test_invariant_6_fraud_throttle_spacing(instrument_id, first_action_utc, second_action_delay_minutes):
    throttle = InstrumentThrottle(min_gap=timedelta(minutes=30))

    throttle.record_action(instrument_id, first_action_utc)

    second_time = first_action_utc + timedelta(minutes=second_action_delay_minutes)

    allowed = throttle.is_allowed(instrument_id, second_time)

    if second_action_delay_minutes >= 30:
        assert allowed is True
    else:
        assert allowed is False