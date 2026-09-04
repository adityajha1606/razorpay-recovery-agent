"""
DND hours helper — Indian National Do Not Disturb timing.

DND hours are 9:00 PM to 9:00 AM IST. Pre-debit notices should be deferred
during this window unless compliance would be violated (e.g., less than 24h
before the earliest affected debit). For demo purposes, we always defer.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DND_START = time(21, 0)
DND_END = time(9, 0)


def is_dnd_hour(now: datetime) -> bool:
    """Return True if `now` (assumed UTC) falls within DND hours in IST."""
    local_time = now.astimezone(IST).time()
    return local_time >= DND_START or local_time < DND_END