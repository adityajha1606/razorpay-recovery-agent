"""
Clock abstraction — builder doc §10.2.

Same code, two speeds. `RealClock` reflects wall‑clock time; `AcceleratedClock`
scales delays by `time_scale` for demos. Both implement the `Clock` protocol.

`advance()` is used by demo endpoints to manually move time forward.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Return the current time, whatever this clock believes it is."""

    @abstractmethod
    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        """Convert a real‑world delay to the clock's notion of delay."""

    @abstractmethod
    def advance(self, delta: timedelta) -> datetime:
        """Move the clock forward. Only meaningful for simulated/accelerated clocks."""
        ...


class RealClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        return real_delay

    def advance(self, delta: timedelta) -> datetime:
        raise RuntimeError("RealClock cannot be advanced — it always reflects wall-clock time")


class AcceleratedClock(Clock):
    def __init__(self, time_scale: int):
        self.time_scale = time_scale
        self._offset = timedelta(0)

    def now(self) -> datetime:
        return datetime.now(timezone.utc) + self._offset

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        return real_delay / self.time_scale

    def advance(self, delta: timedelta) -> datetime:
        self._offset += delta
        return self.now()