"""Unit tests for app/core/clock.py and app/core/config.py (builder doc §10.2, §10.3)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.clock import AcceleratedClock, RealClock
from app.core.config import load_config, parse_duration, parse_time_scale


class TestParsing:
    def test_parse_duration_hours(self) -> None:
        assert parse_duration("72h") == timedelta(hours=72)
        assert parse_duration("0h") == timedelta(hours=0)
        assert parse_duration("168h") == timedelta(hours=168)

    def test_parse_duration_rejects_unknown_format(self) -> None:
        with pytest.raises(ValueError):
            parse_duration("3d")

    def test_parse_time_scale(self) -> None:
        assert parse_time_scale("1x") == 1
        assert parse_time_scale("3600x") == 3600

    def test_parse_time_scale_rejects_unknown_format(self) -> None:
        with pytest.raises(ValueError):
            parse_time_scale("fast")


class TestLoadConfig:
    def test_prod_profile_matches_builder_doc_10_3(self, prod_config) -> None:
        assert prod_config.npci_rules.max_retries == 3
        assert prod_config.npci_rules.notice_lead_time == timedelta(hours=24)
        assert prod_config.self_imposed.retry_spacing == (
            timedelta(hours=0),
            timedelta(hours=72),
            timedelta(hours=168),
        )
        assert prod_config.profile_name == "prod"
        assert prod_config.profile.time_scale == 1

    def test_demo_profile_time_scale(self) -> None:
        demo_config = load_config(profile_name="demo")
        assert demo_config.profile.time_scale == 3600

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ValueError):
            load_config(profile_name="staging")  # type: ignore[arg-type]


class TestRealClock:
    def test_resolve_delay_passes_through_unchanged(self, real_clock: RealClock) -> None:
        delay = timedelta(hours=72)
        assert real_clock.resolve_delay(delay) == delay

    def test_rejects_negative_delay(self, real_clock: RealClock) -> None:
        with pytest.raises(ValueError):
            real_clock.resolve_delay(timedelta(hours=-1))


class TestAcceleratedClock:
    def test_scales_delay_down(self) -> None:
        clock = AcceleratedClock(time_scale=3600)
        assert clock.resolve_delay(timedelta(hours=72)) == timedelta(seconds=72)

    def test_rejects_sub_one_scale(self) -> None:
        with pytest.raises(ValueError):
            AcceleratedClock(time_scale=0)

    def test_rejects_negative_delay(self) -> None:
        clock = AcceleratedClock(time_scale=10)
        with pytest.raises(ValueError):
            clock.resolve_delay(timedelta(hours=-1))