"""Unit tests for the independent compliance verifier."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import load_config
from app.core.verifier import verify_case_compliance
from app.models import AuditEntry, RecoveryCase, assign_bucket

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def config():
    return load_config("prod")


def make_case(amount=10000, bucket="treatment") -> RecoveryCase:
    case = RecoveryCase(
        case_id="pay_test_verifier",
        mandate_id="mandate_verifier",
        instrument_id="instr_verifier",
        original_amount=amount,
        opened_at=datetime.now(timezone.utc),
        state="RECOVERED",
    )
    assign_bucket(case, bucket)  # type: ignore[arg-type]
    return case


def make_audit_entry(
    case_id: str,
    from_state: str,
    to_state: str,
    rule_fired: str,
    timestamp: datetime,
    actor: str = "agent",
    scheduled_at: datetime | None = None,
    sequence_id: int | None = None,
) -> AuditEntry:
    return AuditEntry(
        case_id=case_id,
        from_state=from_state,
        to_state=to_state,
        rule_fired=rule_fired,
        rule_version=2,
        timestamp=timestamp,
        actor=actor,  # type: ignore[arg-type]
        sequence_id=sequence_id,
        scheduled_at=scheduled_at,
    )


def test_verifier_catches_scheduled_peak_time(config):
    case = make_case()
    # Create audit trail with a RETRY_SCHEDULED entry whose scheduled_at is inside peak window (10:00 IST)
    peak_time = datetime(2026, 9, 4, 10, 30, tzinfo=timezone.utc).astimezone(IST)  # 10:30 IST
    # Note: 10:30 IST = 05:00 UTC? Actually IST is 5:30 ahead, so 10:30 IST = 05:00 UTC.
    # We'll create scheduled_at directly as UTC equivalent.
    scheduled_utc = datetime(2026, 9, 4, 5, 0, tzinfo=timezone.utc)  # 10:30 IST
    entry = make_audit_entry(
        case_id=case.case_id,
        from_state="NOTICE_SENT",
        to_state="RETRY_SCHEDULED",
        rule_fired="schedule_retry",
        timestamp=datetime(2026, 9, 4, 4, 0, tzinfo=timezone.utc),  # 09:30 IST, non-peak
        scheduled_at=scheduled_utc,
        sequence_id=1,
    )
    audit = [entry]
    results = verify_case_compliance(case, audit, config)
    peak_result = next(r for r in results if r.rule == "peak_hour_blackout")
    assert peak_result.passed is False


def test_verifier_passes_when_scheduled_outside_peak(config):
    case = make_case()
    scheduled_utc = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)  # 13:30 IST, non-peak
    entry = make_audit_entry(
        case_id=case.case_id,
        from_state="NOTICE_SENT",
        to_state="RETRY_SCHEDULED",
        rule_fired="schedule_retry",
        timestamp=datetime(2026, 9, 4, 4, 0, tzinfo=timezone.utc),
        scheduled_at=scheduled_utc,
    )
    audit = [entry]
    results = verify_case_compliance(case, audit, config)
    peak_result = next(r for r in results if r.rule == "peak_hour_blackout")
    assert peak_result.passed is True


def test_verifier_catches_insufficient_notice_lead(config):
    case = make_case()
    notice_time = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)  # 15:30 IST
    execute_time = notice_time + timedelta(hours=1)  # only 1h gap, less than 24h
    entries = [
        make_audit_entry(
            case_id=case.case_id,
            from_state="NOTICE_PENDING",
            to_state="NOTICE_SENT",
            rule_fired="notice_sent",
            timestamp=notice_time,
        ),
        make_audit_entry(
            case_id=case.case_id,
            from_state="NOTICE_SENT",
            to_state="RETRY_SCHEDULED",
            rule_fired="schedule_retry",
            timestamp=notice_time,
        ),
        make_audit_entry(
            case_id=case.case_id,
            from_state="RETRY_SCHEDULED",
            to_state="RETRY_EXECUTED",
            rule_fired="commit_approved",
            timestamp=execute_time,
        ),
    ]
    results = verify_case_compliance(case, entries, config)
    notice_result = next(r for r in results if r.rule == "notice_lead_time")
    assert notice_result.passed is False