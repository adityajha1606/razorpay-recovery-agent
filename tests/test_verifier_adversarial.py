"""Adversarial tests for the independent verifier.

For each NPCI rule, construct an audit trail that violates that rule and
assert that the verifier catches it. This proves the verifier is not just
catching the one bug we already found, but all claimed rules.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import load_config
from app.core.verifier import verify_case_compliance
from app.models import AuditEntry, RecoveryCase, assign_bucket


@pytest.fixture
def config():
    return load_config("prod")


def make_case(amount=10000, bucket="treatment") -> RecoveryCase:
    case = RecoveryCase(
        case_id="pay_adv",
        mandate_id="mandate_adv",
        instrument_id="instr_adv",
        original_amount=amount,
        opened_at=datetime.now(timezone.utc),
        state="RECOVERED",
    )
    assign_bucket(case, bucket)  # type: ignore[arg-type]
    return case


def make_audit_entry(
    case_id,
    from_state,
    to_state,
    rule_fired,
    timestamp,
    actor="agent",
    scheduled_at=None,
    sequence_id=None,
):
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


def test_verifier_catches_max_retries(config):
    case = make_case()
    case.retries_used = config.npci_rules.max_retries + 1
    audit = [make_audit_entry(case.case_id, "RECEIVED", "CLASSIFIED", "create_case", datetime.now(timezone.utc))]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "max_retries")


def test_verifier_catches_notice_lead_too_short(config):
    case = make_case()
    notice_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    execute_time = notice_time + timedelta(hours=1)  # less than 24h
    audit = [
        make_audit_entry(case.case_id, "NOTICE_PENDING", "NOTICE_SENT", "notice_sent", notice_time),
        make_audit_entry(case.case_id, "NOTICE_SENT", "RETRY_SCHEDULED", "schedule_retry", notice_time),
        make_audit_entry(case.case_id, "RETRY_SCHEDULED", "RETRY_EXECUTED", "commit_approved", execute_time),
    ]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "notice_lead_time")


def test_verifier_catches_retry_spacing(config):
    case = make_case()
    t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t2 = t1 + timedelta(hours=1)  # less than spacing
    audit = [
        make_audit_entry(case.case_id, "RETRY_SCHEDULED", "RETRY_EXECUTED", "commit_approved", t1),
        make_audit_entry(case.case_id, "RETRY_SCHEDULED", "RETRY_EXECUTED", "commit_approved", t2),
    ]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "retry_spacing")


def test_verifier_catches_peak_hour_scheduled(config):
    case = make_case()
    # 10:30 IST is within peak
    scheduled_utc = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
    audit = [
        make_audit_entry(case.case_id, "NOTICE_SENT", "RETRY_SCHEDULED", "schedule_retry",
                         datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc), scheduled_at=scheduled_utc),
    ]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "peak_hour_blackout")


def test_verifier_catches_afa_ceiling(config):
    case = make_case(amount=2000000)  # > default ceiling
    audit = [
        make_audit_entry(case.case_id, "TREATMENT", "NOTICE_PENDING", "technical_decline", datetime.now(timezone.utc)),
        make_audit_entry(case.case_id, "NOTICE_SENT", "RETRY_SCHEDULED", "schedule_retry", datetime.now(timezone.utc)),
    ]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "afa_ceiling")


def test_verifier_catches_bucket_immutability(config):
    case = make_case()
    audit = [
        make_audit_entry(case.case_id, "CLASSIFIED", "TREATMENT", "assign_bucket_treatment", datetime.now(timezone.utc)),
        make_audit_entry(case.case_id, "CLASSIFIED", "CONTROL_HELD", "assign_bucket_control", datetime.now(timezone.utc)),
    ]
    results = verify_case_compliance(case, audit, config)
    assert not next(r.passed for r in results if r.rule == "bucket_immutability")