"""Simple in-memory per-instrument throttle for fraud self-protection (§9C)."""

from __future__ import annotations

from datetime import datetime, timedelta


class InstrumentThrottle:
    """Tracks last action time per instrument and enforces a minimum gap."""

    def __init__(self, min_gap: timedelta):
        self._min_gap = min_gap
        self._last_action: dict[str, datetime] = {}

    def is_allowed(self, instrument_id: str, now: datetime) -> bool:
        """Return True if at least min_gap has passed since last action."""
        last = self._last_action.get(instrument_id)
        if last is None:
            return True
        return (now - last) >= self._min_gap

    def record_action(self, instrument_id: str, now: datetime) -> None:
        """Record a new action time for the instrument."""
        self._last_action[instrument_id] = now