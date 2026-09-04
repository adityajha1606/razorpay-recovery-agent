from datetime import datetime, timezone

from app.core.dnd import is_dnd_hour

def test_is_dnd_hour_true_at_22_ist():
    # 22:00 IST = 16:30 UTC
    now = datetime(2026, 9, 4, 16, 30, tzinfo=timezone.utc)
    assert is_dnd_hour(now) is True

def test_is_dnd_hour_false_at_noon_ist():
    # 12:00 IST = 06:30 UTC
    now = datetime(2026, 9, 4, 6, 30, tzinfo=timezone.utc)
    assert is_dnd_hour(now) is False