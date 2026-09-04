"""Unit tests for app/core/case_store.py."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal  # not used after paise change, but kept for import? no

import pytest

from app.core.case_store import (
    CaseAlreadyExistsError,
    CaseNotFoundError,
    DecisionAlreadyRecordedError,
    FailureAlreadyRecordedError,
    InMemoryCaseStore,
)
from app.models import (
    AuditEntry,
    PaymentFailureEvent,
    RecoveryCase,
    RetryDecision,
)

NOW = datetime.now(timezone.utc)


def make_case(case_id="pay_abc123", **overrides) -> RecoveryCase:
    defaults = dict(
        case_id=case_id,
        mandate_id="mandate_1",
        instrument_id="hashed_instr_1",
        original_amount=49900,
        opened_at=NOW,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


def make_failure(case_id="pay_abc123", attempt=1, payment_id=None) -> PaymentFailureEvent:
    if payment_id is None:
        payment_id = f"pay_retry_{attempt}"
    return PaymentFailureEvent(
        case_id=case_id,
        payment_id=payment_id,
        attempt_number=attempt,
        reason_code="insufficient_funds",
        decline_class="business",
        amount=49900,
        received_at=NOW,
    )


def make_decision(case_id="pay_abc123", attempt=1) -> RetryDecision:
    return RetryDecision(
        case_id=case_id,
        attempt_number=attempt,
        scheduled_at=NOW,
        reasoning="earliest legal slot",
        commit_backend="postgres_outbox",
        commit_ref=f"ref-{case_id}-{attempt}",
    )


class TestCaseStore:
    def test_create_and_get_case(self) -> None:
        store = InMemoryCaseStore()
        case = make_case()
        store.create_case(case)
        assert store.get_case(case.case_id) is case

    def test_create_duplicate_case_raises(self) -> None:
        store = InMemoryCaseStore()
        store.create_case(make_case(case_id="pay_dup"))
        with pytest.raises(CaseAlreadyExistsError):
            store.create_case(make_case(case_id="pay_dup"))

    def test_create_case_with_same_mandate_raises(self) -> None:
        store = InMemoryCaseStore()
        store.create_case(make_case(case_id="pay_a", mandate_id="mandate_shared"))
        with pytest.raises(CaseAlreadyExistsError):
            store.create_case(make_case(case_id="pay_b", mandate_id="mandate_shared"))

    def test_find_case_by_mandate(self) -> None:
        store = InMemoryCaseStore()
        case = make_case(case_id="pay_find", mandate_id="mandate_find")
        store.create_case(case)
        found = store.find_case_by_mandate("mandate_find")
        assert found is case
        assert store.find_case_by_mandate("nonexistent") is None

    def test_find_case_by_payment_id(self) -> None:
        store = InMemoryCaseStore()
        case = make_case(case_id="pay_paymentid")
        store.create_case(case)
        assert store.find_case_by_payment_id("pay_paymentid") is case
        assert store.find_case_by_payment_id("not_a_case") is None

    def test_get_max_attempt_number(self) -> None:
        store = InMemoryCaseStore()
        store.record_failure(make_failure(case_id="pay_max", attempt=1))
        store.record_failure(make_failure(case_id="pay_max", attempt=3))
        assert store.get_max_attempt_number("pay_max") == 3
        assert store.get_max_attempt_number("pay_unknown") == 0

    def test_record_failure_and_get(self) -> None:
        store = InMemoryCaseStore()
        event = make_failure()
        store.record_failure(event)
        assert store.get_failure(event.case_id, event.attempt_number) is event

    def test_duplicate_failure_raises(self) -> None:
        store = InMemoryCaseStore()
        event = make_failure()
        store.record_failure(event)
        with pytest.raises(FailureAlreadyRecordedError):
            store.record_failure(event)

    def test_record_decision_and_get(self) -> None:
        store = InMemoryCaseStore()
        decision = make_decision()
        store.record_retry_decision(decision)
        assert store.get_retry_decision(decision.case_id, decision.attempt_number) is decision

    def test_duplicate_decision_raises(self) -> None:
        store = InMemoryCaseStore()
        decision = make_decision()
        store.record_retry_decision(decision)
        with pytest.raises(DecisionAlreadyRecordedError):
            store.record_retry_decision(decision)

    def test_append_audit_and_get_trail(self) -> None:
        store = InMemoryCaseStore()
        entry = AuditEntry(
            case_id="pay_audit",
            from_state="RECEIVED",
            to_state="CLASSIFIED",
            rule_fired="assign_bucket",
            rule_version=1,
            timestamp=NOW,
            actor="agent",
        )
        store.append_audit(entry)
        trail = store.get_audit_trail("pay_audit")
        assert len(trail) == 1
        assert trail[0].sequence_id == 1

    def test_get_pending_retries(self) -> None:
        store = InMemoryCaseStore()
        pending = make_decision(case_id="pay_pending", attempt=2)
        done = make_decision(case_id="pay_done", attempt=1)
        done.outcome = "recovered"
        store.record_retry_decision(pending)
        store.record_retry_decision(done)
        assert len(store.get_pending_retries()) == 1
        assert store.get_pending_retries()[0].case_id == "pay_pending"