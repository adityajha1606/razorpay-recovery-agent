"""
Clock abstraction — builder doc §10.2.

Implements the two-clock rule (§2): the code running in the live demo and
the code claimed compliant must be the *same* code, at two different speeds,
never two different implementations. `RealClock` and `AcceleratedClock` share
this one interface; nothing downstream should ever branch on which one it
has — only the returned delay differs.

The §8 property tests must always be exercised through `RealClock` under the
`prod` profile, regardless of which Clock the running app is wired to
(see CLAUDE.md rule 3).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Time source used everywhere business logic needs "now" or "how long
    should I actually wait" — never call `datetime.now()` or `time.sleep()`
    directly outside an implementation of this protocol."""

    def now(self) -> datetime:
        """Current time, timezone-aware (UTC)."""
        ...

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        """Translate a real-world delay (e.g. the 72h NPCI spacing floor)
        into the delay this clock should actually wait."""
        ...


class RealClock:
    """Passes real durations through unchanged. Wired to the `prod` profile
    and used by every §8 Hypothesis invariant — tests never exercise
    `AcceleratedClock`, so the compliance claim stays true independent of
    demo speed."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        if real_delay < timedelta(0):
            raise ValueError(f"real_delay cannot be negative, got {real_delay}")
        return real_delay


class AcceleratedClock:
    """Scales real durations by `time_scale` for the live demo (e.g. a
    3600x scale turns a 72h spacing floor into 72 real seconds). Runs the
    exact same scheduling logic as `RealClock` — only the returned delay
    differs, per the two-clock rule (§2)."""

    def __init__(self, time_scale: int) -> None:
        if time_scale < 1:
            raise ValueError(f"time_scale must be >= 1, got {time_scale}")
        self._time_scale = time_scale

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def resolve_delay(self, real_delay: timedelta) -> timedelta:
        if real_delay < timedelta(0):
            raise ValueError(f"real_delay cannot be negative, got {real_delay}")
        return real_delay / self._time_scale
