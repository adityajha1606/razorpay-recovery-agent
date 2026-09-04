"""Unit tests for app/models.py (builder doc §6)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import (
    AuditEntry,
    NoticeRecord,
    PaymentFailureEvent,
    RecoveryCase,
    RetryDecision,
    assign_bucket,
)

NOW = datetime.now(timezone.utc)


def make_case(**overrides) -> RecoveryCase:
    defaults = dict(
        case_id="pay_abc123",
        mandate_id="mandate_1",
        instrument_id="hashed_instr_1",
        original_amount=49900,  # ₹499.00 in paise
        opened_at=NOW,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


class TestRecoveryCase:
    def test_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError):
            make_case(original_amount=0)

    def test_rejects_empty_case_id(self) -> None:
        with pytest.raises(ValueError):
            make_case(case_id="")

    def test_rejects_empty_mandate_id(self) -> None:
        with pytest.raises(ValueError):
            make_case(mandate_id="")

    def test_rejects_empty_instrument_id(self) -> None:
        with pytest.raises(ValueError):
            make_case(instrument_id="")

    def test_rejects_negative_retries_used(self) -> None:
        with pytest.raises(ValueError):
            make_case(retries_used=-1)


class TestAssignBucket:
    def test_sets_bucket_first_time(self) -> None:
        case = make_case()
        assign_bucket(case, "treatment")
        assert case.bucket == "treatment"

    def test_reassigning_same_bucket_is_noop(self) -> None:
        case = make_case()
        assign_bucket(case, "control")
        assign_bucket(case, "control")
        assert case.bucket == "control"

    def test_changing_bucket_raises_invariant_7(self) -> None:
        case = make_case()
        assign_bucket(case, "treatment")
        with pytest.raises(ValueError, match="Invariant 7"):
            assign_bucket(case, "control")
        assert case.bucket == "treatment"


class TestPaymentFailureEvent:
    def test_accepts_valid_event(self) -> None:
        event = PaymentFailureEvent(
            case_id="pay_abc123",
            payment_id="pay_abc123_r1",
            attempt_number=1,
            reason_code="insufficient_funds",
            decline_class="business",
            amount=49900,  # paise
            received_at=NOW,
        )
        assert event.amount == 49900

    def test_rejects_attempt_number_below_one(self) -> None:
        with pytest.raises(ValueError):
            PaymentFailureEvent(
                case_id="pay_abc123",
                payment_id="pay_abc123_r1",
                attempt_number=0,
                reason_code="insufficient_funds",
                decline_class="business",
                amount=49900,
                received_at=NOW,
            )

    def test_rejects_non_positive_amount(self) -> None:
        with pytest.raises(ValueError):
            PaymentFailureEvent(
                case_id="pay_abc123",
                payment_id="pay_abc123_r1",
                attempt_number=1,
                reason_code="insufficient_funds",
                decline_class="business",
                amount=0,
                received_at=NOW,
            )


class TestRetryDecision:
    def test_requires_non_empty_reasoning(self) -> None:
        with pytest.raises(ValueError):
            RetryDecision(
                case_id="pay_abc123",
                attempt_number=1,
                scheduled_at=NOW,
                reasoning="",
                commit_backend="postgres_outbox",
                commit_ref="ref-1",
            )

    def test_requires_non_empty_commit_ref(self) -> None:
        with pytest.raises(ValueError):
            RetryDecision(
                case_id="pay_abc123",
                attempt_number=1,
                scheduled_at=NOW,
                reasoning="earliest legal slot per §9B",
                commit_backend="postgres_outbox",
                commit_ref="",
            )


class TestAuditEntry:
    def test_requires_rule_fired(self) -> None:
        with pytest.raises(ValueError):
            AuditEntry(
                case_id="pay_abc123",
                from_state="RECEIVED",
                to_state="CLASSIFIED",
                rule_fired="",
                rule_version=1,
                timestamp=NOW,
                actor="agent",
            )

    def test_sequence_id_optional(self) -> None:
        entry = AuditEntry(
            case_id="pay_abc123",
            from_state="RECEIVED",
            to_state="CLASSIFIED",
            rule_fired="assign_bucket",
            rule_version=1,
            timestamp=NOW,
            actor="agent",
        )
        assert entry.sequence_id is None


class TestNoticeRecord:
    def test_requires_at_least_one_mandate(self) -> None:
        with pytest.raises(ValueError):
            NoticeRecord(instrument_id="hashed_instr_1", mandate_ids=[])