"""
Webhook parser — maps Razorpay payment.failed and payment.captured payloads.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from app.models import PaymentFailureEvent, PaymentSuccessEvent, RecoveryCase


def parse_payment_failed(
    payload: dict,
    existing_case: Optional[RecoveryCase] = None,
    attempt_number: Optional[int] = None,
) -> PaymentFailureEvent:
    """
    Extract a PaymentFailureEvent from the raw webhook payload.
    """
    payment = payload["payload"]["payment"]["entity"]

    method = payment.get("method")
    if method != "upi":
        raise ValueError(
            f"parse_payment_failed only handles UPI payments; got method={method!r}. "
            f"Non-UPI payment.failed events must be filtered before reaching this parser."
        )

    currency = payment.get("currency")
    if currency != "INR":
        raise ValueError(f"expected currency INR, got {currency!r}")

    payment_id = payment["id"]
    amount_paise = int(payment["amount"])
    if amount_paise <= 0:
        raise ValueError(f"amount_paise must be positive, got {amount_paise}")

    vpa = payment.get("vpa")
    if not vpa:
        raise ValueError("UPI payment.failed event missing 'vpa' — cannot derive instrument_id")
    instrument_id = hashlib.sha256(vpa.encode("utf-8")).hexdigest()

    notes = payment.get("notes") or {}
    mandate_id = notes.get("mandate_id")  # None if absent

    reason_code = payment.get("error_code", "")
    error_reason = payment.get("error_reason")
    error_source = payment.get("error_source")
    error_step = payment.get("error_step")

    if existing_case is not None:
        case_id = existing_case.case_id
        if attempt_number is None:
            raise ValueError("attempt_number must be provided when existing_case is given")
    else:
        case_id = payment_id
        if attempt_number is None:
            attempt_number = 1
        elif attempt_number != 1:
            raise ValueError("attempt_number must be 1 when no existing_case is provided")

    if attempt_number < 1:
        raise ValueError(f"attempt_number must be >= 1, got {attempt_number}")

    created_at = datetime.fromtimestamp(int(payment["created_at"]), tz=timezone.utc)

    decline_class = "unclassified"

    return PaymentFailureEvent(
        case_id=case_id,
        payment_id=payment_id,
        attempt_number=attempt_number,
        reason_code=reason_code,
        decline_class=decline_class,
        amount=amount_paise,
        received_at=created_at,
        mandate_id=mandate_id,
        instrument_id=instrument_id,
        error_reason=error_reason,
        error_source=error_source,
        error_step=error_step,
        notes=notes,
    )


def parse_payment_captured(payload: dict, case: RecoveryCase) -> PaymentSuccessEvent:
    """
    Extract a PaymentSuccessEvent from a Razorpay `payment.captured` webhook.
    The caller must already have resolved the case (e.g., by mandate_id).
    """
    payment = payload["payload"]["payment"]["entity"]
    payment_id = payment["id"]
    amount_paise = int(payment["amount"])
    if amount_paise <= 0:
        raise ValueError(f"amount_paise must be positive, got {amount_paise}")

    captured_at = datetime.fromtimestamp(int(payment["created_at"]), tz=timezone.utc)

    return PaymentSuccessEvent(
        case_id=case.case_id,
        payment_id=payment_id,
        amount=amount_paise,
        captured_at=captured_at,
    )