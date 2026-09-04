"""Unit tests for app/core/webhook_security.py."""

from __future__ import annotations

import hashlib
import hmac

from app.core.webhook_security import verify_webhook_signature

SECRET = "test_secret_123"


def _sign(body: bytes) -> str:
    return hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


class TestVerifyWebhookSignature:
    def test_valid_signature_returns_true(self) -> None:
        body = b'{"event":"payment.failed"}'
        sig = _sign(body)
        assert verify_webhook_signature(body, sig, SECRET) is True

    def test_tampered_body_returns_false(self) -> None:
        original = b'{"event":"payment.failed"}'
        tampered = b'{"event":"payment.failed","extra":1}'
        sig = _sign(original)
        assert verify_webhook_signature(tampered, sig, SECRET) is False

    def test_missing_secret_returns_false(self) -> None:
        body = b'{"event":"payment.failed"}'
        sig = _sign(body)
        assert verify_webhook_signature(body, sig, "") is False

    def test_missing_signature_header_returns_false(self) -> None:
        body = b'{"event":"payment.failed"}'
        assert verify_webhook_signature(body, None, SECRET) is False

    def test_wrong_secret_returns_false(self) -> None:
        body = b'{"event":"payment.failed"}'
        sig = _sign(body)
        assert verify_webhook_signature(body, sig, "different_secret") is False