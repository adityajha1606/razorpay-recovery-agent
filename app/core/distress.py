"""Cross-mandate distress signal — detect account-level anomalies.

If the same instrument (hashed VPA) fails multiple times within a short
window, it may indicate the account itself is in trouble, not just a
single mandate. Retrying is futile. This module provides a simple
threshold detector that can be queried by the throttle or dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta

class DistressDetector:
    """Sliding window failure counter per instrument."""

    def __init__(self, window: timedelta, threshold: int):
        self.window = window
        self.threshold = threshold
        self._failures: dict[str, list[datetime]] = {}

    def record_failure(self, instrument_id: str, now: datetime) -> None:
        """Record a failure occurrence for an instrument."""
        if instrument_id not in self._failures:
            self._failures[instrument_id] = []
        self._failures[instrument_id].append(now)
        cutoff = now - self.window
        self._failures[instrument_id] = [
            t for t in self._failures[instrument_id] if t >= cutoff
        ]

    def is_distressed(self, instrument_id: str, now: datetime) -> bool:
        """Return True if failure count exceeds threshold within window."""
        events = self._failures.get(instrument_id, [])
        cutoff = now - self.window
        recent = [t for t in events if t >= cutoff]
        return len(recent) >= self.threshold

    def failure_count(self, instrument_id: str, now: datetime) -> int:
        events = self._failures.get(instrument_id, [])
        cutoff = now - self.window
        return len([t for t in events if t >= cutoff])