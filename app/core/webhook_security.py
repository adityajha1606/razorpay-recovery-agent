"""
Webhook signature verification — Razorpay HMAC-SHA256.

Razorpay signs every webhook with HMAC-SHA256 over the raw request body and
puts the signature in the `X-Razorpay-Signature` header. This module provides
a single function to verify that signature using the webhook secret. The
comparison is constant-time via `hmac.compare_digest` to avoid leaking
timing information.

Usage:
    from app.core.webhook_security import verify_webhook_signature
    if not verify_webhook_signature(raw_body, signature_header, secret):
        raise HTTPException(401, "Invalid signature")
"""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(
    raw_body: bytes,
    signature_header: str | None,
    secret: str,
) -> bool:
    """Return True if the signature is valid for the given raw body.

    Args:
        raw_body: The raw, unparsed request body bytes (exactly as received).
        signature_header: Value of the `X-Razorpay-Signature` header, or None.
        secret: The webhook secret from Razorpay Dashboard / environment.

    Returns:
        True if secret and signature are present and the signature matches.
        False otherwise (including missing secret or header — fail closed).
    """
    if not secret or not signature_header:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature_header)