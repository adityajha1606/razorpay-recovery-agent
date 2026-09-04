"""Unit tests for app/core/decline_router.py (builder doc §9A)."""

from __future__ import annotations

from datetime import timedelta, time

import pytest

from app.core.config import AppConfig, DeclineRules, NpciRules, PeakWindow, ProfileConfig
from app.core.decline_router import classify_failure


def _make_config(decline_rules: DeclineRules | None = None) -> AppConfig:
    if decline_rules is None:
        decline_rules = DeclineRules(
            technical=("bank_server_down", "network_timeout"),
            business=("insufficient_funds", "blocked_account"),
            default="business",
        )
    return AppConfig(
        npci_rules=NpciRules(
            max_retries=3,
            notice_lead_time=timedelta(hours=24),
            spacing=(timedelta(hours=0), timedelta(hours=72), timedelta(hours=168)),
            control_observation_window=timedelta(hours=72),
            max_schedule_window=timedelta(hours=48),
            peak_windows=(
                PeakWindow(start=time(10, 0), end=time(13, 0)),
                PeakWindow(start=time(17, 0), end=time(21, 30)),
            ),
            afa_free_ceiling=1500000,
        ),
        profile_name="prod",
        profile=ProfileConfig(time_scale=1),
        decline_rules=decline_rules,
    )


class TestClassifyFailure:
    def test_known_technical_code_returns_technical_retryable(self) -> None:
        config = _make_config()
        assert classify_failure(config, "bank_server_down") == ("technical", True)

    def test_known_business_code_returns_business_non_retryable(self) -> None:
        config = _make_config()
        assert classify_failure(config, "insufficient_funds") == ("business", False)

    def test_unknown_code_defaults_to_business_non_retryable(self) -> None:
        config = _make_config()
        assert classify_failure(config, "totally_unknown") == ("business", False)

    def test_falls_back_to_error_reason_if_reason_code_unknown(self) -> None:
        config = _make_config()
        assert classify_failure(config, "UNKNOWN", error_reason="blocked_account") == ("business", False)

    def test_case_insensitive_matching(self) -> None:
        config = _make_config()
        assert classify_failure(config, "BANK_SERVER_DOWN") == ("technical", True)

    def test_empty_decline_rules_uses_safe_default(self) -> None:
        config = _make_config(decline_rules=DeclineRules(technical=(), business=(), default="business"))
        assert classify_failure(config, "bank_server_down") == ("business", False)