"""
Decline router — builder doc §9A.
"""

from __future__ import annotations

from app.core.config import AppConfig
from app.models import DeclineClass


def classify_failure(
    config: AppConfig,
    reason_code: str,
    error_reason: str | None = None,
) -> tuple[DeclineClass, bool]:
    """
    Classify a payment failure. decline_rules is a required AppConfig field,
    so this never has to guard against a missing config.
    """
    code = reason_code.strip().lower() if reason_code else ""
    reason = error_reason.strip().lower() if error_reason else ""

    rules = config.decline_rules
    technical_codes = {c.lower() for c in rules.technical}
    business_codes = {c.lower() for c in rules.business}

    for candidate in (code, reason):
        if candidate in technical_codes:
            return ("technical", True)
        if candidate in business_codes:
            return ("business", False)

    if rules.default == "technical":
        return ("technical", True)
    return ("business", False)