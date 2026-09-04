"""Unit tests for app/core/webhook_parser.py."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from app.core.webhook_parser import parse_payment_failed
from app.models import RecoveryCase

SAMPLE_PAYLOAD = {
    "entity": "event",
    "event": "payment.failed",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_TEST123",
                "amount": 50000,
                "currency": "INR",
                "method": "upi",
                "vpa": "test@okhdfcbank",
                "error_code": "BAD_REQUEST_ERROR",
                "error_reason": "payment_failed",
                "error_source": "issuer",
                "error_step": "payment_authorization",
                "created_at": 1699999999,
                "notes": {"mandate_id": "mandate_1"},
            }
        }
    },
}


class TestParsePaymentFailed:
    def test_parses_valid_upi_payload(self) -> None:
        event = parse_payment_failed(SAMPLE_PAYLOAD)
        assert event.payment_id == "pay_TEST123"
        assert event.case_id == "pay_TEST123"
        assert event.attempt_number == 1
        assert event.amount == 50000
        assert event.received_at == datetime.fromtimestamp(1699999999, tz=timezone.utc)
        assert event.mandate_id == "mandate_1"
        expected_instrument = hashlib.sha256(b"test@okhdfcbank").hexdigest()
        assert event.instrument_id == expected_instrument
        assert event.reason_code == "BAD_REQUEST_ERROR"
        assert event.error_reason == "payment_failed"
        assert event.error_source == "issuer"
        assert event.error_step == "payment_authorization"
        assert event.notes == {"mandate_id": "mandate_1"}

    def test_missing_vpa_raises_value_error(self) -> None:
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_TEST456",
                "amount": 100,
                "currency": "INR",
                "method": "upi",
                # no vpa key
                "error_code": "BAD_REQUEST_ERROR",
                "created_at": 1699999999,
                "notes": {},
            }}}
        }
        with pytest.raises(ValueError):
            parse_payment_failed(payload)

    def test_missing_notes_yields_none_mandate_id(self) -> None:
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_TEST789",
                "amount": 100,
                "currency": "INR",
                "method": "upi",
                "vpa": "a@upi",
                "error_code": "BAD_REQUEST_ERROR",
                "created_at": 1699999999,
                # no notes
            }}}
        }
        event = parse_payment_failed(payload)
        assert event.mandate_id is None
        assert event.notes == {}

    def test_amount_conversion_handles_integer_paise(self) -> None:
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_TEST000",
                "amount": 1,
                "currency": "INR",
                "method": "upi",
                "vpa": "a@upi",
                "error_code": "BAD_REQUEST_ERROR",
                "created_at": 1699999999,
            }}}
        }
        event = parse_payment_failed(payload)
        assert event.amount == 1

    def test_repeat_event_with_existing_case_uses_provided_case_id_and_attempt(self) -> None:
        existing_case = RecoveryCase(
            case_id="pay_ORIGINAL123",
            mandate_id="mandate_1",
            instrument_id="hashed_instr_1",
            original_amount=50000,
            opened_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_RETRY999",
                "amount": 50000,
                "currency": "INR",
                "method": "upi",
                "vpa": "test@okhdfcbank",
                "error_code": "BAD_REQUEST_ERROR",
                "error_reason": "payment_failed",
                "created_at": 1700000000,
                "notes": {"mandate_id": "mandate_1"},
            }}}
        }
        event = parse_payment_failed(payload, existing_case=existing_case, attempt_number=2)
        assert event.case_id == "pay_ORIGINAL123"
        assert event.payment_id == "pay_RETRY999"
        assert event.attempt_number == 2
        assert event.mandate_id == "mandate_1"

    def test_repeat_event_requires_attempt_number_when_existing_case_given(self) -> None:
        existing_case = RecoveryCase(
            case_id="pay_ORIGINAL123",
            mandate_id="mandate_1",
            instrument_id="hashed_instr_1",
            original_amount=50000,
            opened_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_RETRY999",
                "amount": 50000,
                "currency": "INR",
                "method": "upi",
                "vpa": "test@okhdfcbank",
                "error_code": "BAD_REQUEST_ERROR",
                "created_at": 1700000000,
                "notes": {"mandate_id": "mandate_1"},
            }}}
        }
        with pytest.raises(ValueError):
            parse_payment_failed(payload, existing_case=existing_case)

    def test_no_existing_case_with_attempt_greater_than_one_raises(self) -> None:
        payload = {
            "payload": {"payment": {"entity": {
                "id": "pay_TEST123",
                "amount": 50000,
                "currency": "INR",
                "method": "upi",
                "vpa": "test@okhdfcbank",
                "error_code": "BAD_REQUEST_ERROR",
                "created_at": 1699999999,
                "notes": {},
            }}}
        }
        with pytest.raises(ValueError):
            parse_payment_failed(payload, attempt_number=2)