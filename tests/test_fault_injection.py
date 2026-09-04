"""Fault-injected property tests — simulated commit failures during retries.

These tests extend the invariant suite by randomly failing the commit
backend during execution and asserting that the state machine still obeys
the retry cap and does not increment retries_used on failed commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from app.core.case_store import InMemoryCaseStore
from app.core.config import load_config
from app.core.state_machine import StateMachine
from app.models import (
    PaymentFailureEvent,
    RecoveryCase,
    RetryDecision,
    assign_bucket,
)


class FixedClock:
    def __init__(self):
        self.current = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        return real_delay

    def advance(self, delta: timedelta):
        self.current += delta


class ScriptedCommitBackend:
    """Commit backend that returns values from a scripted list."""

    def __init__(self, results: list[bool]):
        self._results = list(results)
        self._index = 0

    def commit(self, action: RetryDecision) -> bool:
        if self._index >= len(self._results):
            return False  # fail closed if script exhausted
        result = self._results[self._index]
        self._index += 1
        return result


def make_failure(case_id: str, attempt: int, reason: str = "bank_server_down", amount: int = 10000) -> PaymentFailureEvent:
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


def _sm():
    return StateMachine()


def _config():
    return load_config(profile_name="prod")


@settings(max_examples=100)
@given(
    case_id=st.text(min_size=1, max_size=20),
    amount=st.integers(min_value=1, max_value=100000),
    commit_results=st.lists(st.booleans(), min_size=1, max_size=10),
)
def test_fault_injection_retry_cap_and_commit_gating(case_id, amount, commit_results):
    sm = _sm()
    config = _config()
    clock = FixedClock()
    backend = ScriptedCommitBackend(commit_results)

    # Initial failure
    event = make_failure(case_id, 1, "bank_server_down", amount)
    case, _, _ = sm.handle_payment_failed(None, event, config, clock)
    assert case.state == "NOTICE_PENDING" or case.state == "AFA_REQUIRED" or case.state == "ESCALATED"
    if case.state != "NOTICE_PENDING":
        return  # not a retryable case, skip

    successful_commits = 0

    while case.state == "NOTICE_PENDING" and successful_commits < config.npci_rules.max_retries:
        # Send notice -> RETRY_SCHEDULED
        case, _, decision = sm.mark_notice_sent(case, config, clock)
        assert decision is not None
        assert case.state == "RETRY_SCHEDULED"

        retries_before = case.retries_used
        # Attempt execution with scripted commit result
        case, _, executed = sm.attempt_execution(case, decision, backend, config, clock)
        if executed:
            successful_commits += 1
            assert case.state == "RETRY_EXECUTED"
            # Simulate a new failure event (outcome)
            new_event = make_failure(case_id, successful_commits + 1, "bank_server_down", amount)
            case, _, _ = sm.handle_payment_failed(case, new_event, config, clock)
            assert case.retries_used == retries_before + 1  # only incremented on genuine execution
        else:
            # Commit failed; state must remain RETRY_SCHEDULED and retries unchanged
            assert case.state == "RETRY_SCHEDULED"
            assert case.retries_used == retries_before

    # Final invariant: retries_used never exceeds max_retries
    assert case.retries_used <= config.npci_rules.max_retries