"""
Config loading — builder doc §10.3.

Loads `config/npci_rules.yaml` and exposes it as a typed `AppConfig`, with
durations parsed into real `timedelta` objects and decline rules into a
frozen dataclass so callers never hand-parse "72h"-style strings or access
raw dicts. This is the one place that file gets read; everything else
(decline router, retry optimizer, throttles) should take an `AppConfig` as
a parameter rather than opening the YAML itself, so the rule-citation
receipt (§9F) always has one canonical source to point back to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from typing import Literal

import yaml

_DURATION_RE = re.compile(r"^(\d+)h$")
_SCALE_RE = re.compile(r"^(\d+)x$")
_CLOCK_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "npci_rules.yaml"

ProfileName = Literal["prod", "demo"]


def parse_duration(value: str) -> timedelta:
    match = _DURATION_RE.match(value.strip())
    if not match:
        raise ValueError(f"Unsupported duration format: {value!r} (expected e.g. '72h')")
    return timedelta(hours=int(match.group(1)))


def parse_time_scale(value: str) -> int:
    match = _SCALE_RE.match(value.strip())
    if not match:
        raise ValueError(f"Unsupported time_scale format: {value!r} (expected e.g. '3600x')")
    return int(match.group(1))


def parse_clock_time(value: str) -> time:
    """Parse a 24h 'HH:MM' string (IST wall-clock) into a `time`.
    Whatever consumes NpciRules.peak_windows must convert Clock.now() to
    IST before comparing — these are wall-clock times, not UTC."""
    match = _CLOCK_TIME_RE.match(value.strip())
    if not match:
        raise ValueError(f"Unsupported clock time format: {value!r} (expected e.g. '10:00')")
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


@dataclass(frozen=True)
class PeakWindow:
    """One NPCI non-peak-execution blackout window, IST wall-clock."""
    start: time
    end: time


@dataclass(frozen=True)
class NpciRules:
    """The regulatory floor everything else in the system operates inside."""

    max_retries: int
    notice_lead_time: timedelta
    spacing: tuple[timedelta, ...]
    control_observation_window: timedelta
    max_schedule_window: timedelta
    peak_windows: tuple[PeakWindow, ...]
    afa_free_ceiling: int  # integer paise


@dataclass(frozen=True)
class DeclineRules:
    technical: tuple[str, ...]
    business: tuple[str, ...]
    default: str  # "technical" or "business"


@dataclass(frozen=True)
class ProfileConfig:
    time_scale: int


@dataclass(frozen=True)
class AppConfig:
    npci_rules: NpciRules
    profile_name: ProfileName
    profile: ProfileConfig
    decline_rules: DeclineRules


def load_config(profile_name: ProfileName = "prod", path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.exists():
        raise FileNotFoundError(f"NPCI config not found at {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "npci_rules" not in raw or "profiles" not in raw:
        raise ValueError(f"{path} is missing required top-level keys 'npci_rules'/'profiles'")

    if profile_name not in raw["profiles"]:
        raise ValueError(
            f"Unknown profile {profile_name!r}; expected one of {sorted(raw['profiles'])}"
        )

    npci_raw = raw["npci_rules"]
    npci_rules = NpciRules(
        max_retries=int(npci_raw["max_retries"]),
        notice_lead_time=parse_duration(str(npci_raw["notice_lead_time"])),
        spacing=tuple(parse_duration(str(s)) for s in npci_raw["spacing"]),
        control_observation_window=parse_duration(str(npci_raw["control_observation_window"])),
        max_schedule_window=parse_duration(str(npci_raw["max_schedule_window"])),
        peak_windows=tuple(
            PeakWindow(start=parse_clock_time(str(w["start"])), end=parse_clock_time(str(w["end"])))
            for w in npci_raw.get("peak_windows", [])
        ),
        afa_free_ceiling=int(npci_raw["afa_free_ceiling"]),
    )

    profile_raw = raw["profiles"][profile_name]
    profile = ProfileConfig(time_scale=parse_time_scale(str(profile_raw["time_scale"])))

    decline_raw = raw.get("decline_rules") or {}
    default_class = str(decline_raw.get("default", "business"))
    if default_class not in ("technical", "business"):
        raise ValueError(
            f"decline_rules.default must be 'technical' or 'business', got {default_class!r}"
        )
    decline_rules = DeclineRules(
        technical=tuple(decline_raw.get("technical", [])),
        business=tuple(decline_raw.get("business", [])),
        default=default_class,
    )

    return AppConfig(
        npci_rules=npci_rules,
        profile_name=profile_name,
        profile=profile,
        decline_rules=decline_rules,
    )