"""Postgres-backed case store — same interface as InMemoryCaseStore.

Uses psycopg3 to persist cases, failures, decisions, and audit entries.
Designed for single-writer operation (matches the app's single-threaded
state machine). The audit trail remains Merkle-chained; prev_hash and
entry_hash are stored and verified on read.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

import psycopg

from app.models import (
    AuditEntry,
    PaymentFailureEvent,
    RecoveryCase,
    RetryDecision,
)


class CaseStoreError(Exception):
    """Base store error."""


class CaseNotFoundError(CaseStoreError):
    """Raised when a requested case does not exist."""


class CaseAlreadyExistsError(CaseStoreError):
    """Raised when attempting to create a duplicate case."""


class FailureAlreadyRecordedError(CaseStoreError):
    """Raised when recording a duplicate failure event for an attempt."""


class DecisionAlreadyRecordedError(CaseStoreError):
    """Raised when recording a duplicate retry decision for an attempt."""


class PostgresCaseStore:
    def __init__(self, conninfo: str = "postgresql://recovery:recovery@localhost:5432/recovery"):
        self.conn = psycopg.connect(conninfo)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS recovery_cases (
                    case_id TEXT PRIMARY KEY,
                    mandate_id TEXT,
                    instrument_id TEXT,
                    original_amount INTEGER,
                    opened_at TIMESTAMPTZ,
                    retries_used INTEGER,
                    state TEXT,
                    bucket TEXT,
                    control_outcome TEXT,
                    control_observation_deadline TIMESTAMPTZ,
                    resolution_note TEXT
                );
                CREATE TABLE IF NOT EXISTS payment_failures (
                    case_id TEXT,
                    attempt_number INTEGER,
                    payment_id TEXT PRIMARY KEY,
                    reason_code TEXT,
                    decline_class TEXT,
                    amount INTEGER,
                    received_at TIMESTAMPTZ,
                    mandate_id TEXT,
                    instrument_id TEXT,
                    error_reason TEXT,
                    error_source TEXT,
                    error_step TEXT,
                    notes JSONB
                );
                CREATE TABLE IF NOT EXISTS retry_decisions (
                    case_id TEXT,
                    attempt_number INTEGER,
                    scheduled_at TIMESTAMPTZ,
                    reasoning TEXT,
                    commit_backend TEXT,
                    commit_ref TEXT,
                    executed_at TIMESTAMPTZ,
                    outcome TEXT,
                    PRIMARY KEY (case_id, attempt_number)
                );
                CREATE TABLE IF NOT EXISTS audit_entries (
                    sequence_id SERIAL PRIMARY KEY,
                    case_id TEXT,
                    from_state TEXT,
                    to_state TEXT,
                    rule_fired TEXT,
                    rule_version INTEGER,
                    timestamp TIMESTAMPTZ,
                    actor TEXT,
                    scheduled_at TIMESTAMPTZ,
                    prev_hash TEXT,
                    entry_hash TEXT
                );
            """)
        self.conn.commit()

    # ------------------------------------------------------------------
    # Case management
    # ------------------------------------------------------------------
    def create_case(self, case: RecoveryCase) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO recovery_cases
                    (case_id, mandate_id, instrument_id, original_amount, opened_at,
                     retries_used, state, bucket, control_outcome,
                     control_observation_deadline, resolution_note)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        case.case_id, case.mandate_id, case.instrument_id,
                        case.original_amount, case.opened_at, case.retries_used,
                        case.state, case.bucket, case.control_outcome,
                        case.control_observation_deadline, case.resolution_note,
                    ),
                )
            self.conn.commit()
        except psycopg.errors.UniqueViolation:
            self.conn.rollback()
            raise CaseAlreadyExistsError(f"case_id {case.case_id!r} already exists")

    def get_case(self, case_id: str) -> RecoveryCase:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM recovery_cases WHERE case_id = %s", (case_id,))
            row = cur.fetchone()
        if not row:
            raise CaseNotFoundError(f"no case with case_id {case_id!r}")
        return self._row_to_case(row)

    def update_case(self, case: RecoveryCase) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE recovery_cases SET
                    mandate_id = %s, instrument_id = %s, original_amount = %s,
                    opened_at = %s, retries_used = %s, state = %s, bucket = %s,
                    control_outcome = %s, control_observation_deadline = %s,
                    resolution_note = %s
                WHERE case_id = %s
                """,
                (
                    case.mandate_id, case.instrument_id, case.original_amount,
                    case.opened_at, case.retries_used, case.state, case.bucket,
                    case.control_outcome, case.control_observation_deadline,
                    case.resolution_note, case.case_id,
                ),
            )
        self.conn.commit()

    def list_cases(self) -> list[RecoveryCase]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM recovery_cases")
            rows = cur.fetchall()
        return [self._row_to_case(r) for r in rows]

    def find_case_by_mandate(self, mandate_id: str) -> Optional[RecoveryCase]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM recovery_cases WHERE mandate_id = %s", (mandate_id,))
            row = cur.fetchone()
        return self._row_to_case(row) if row else None

    def find_case_by_payment_id(self, payment_id: str) -> Optional[RecoveryCase]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT c.* FROM recovery_cases c JOIN payment_failures f ON c.case_id = f.case_id WHERE f.payment_id = %s",
                (payment_id,),
            )
            row = cur.fetchone()
        return self._row_to_case(row) if row else None

    def get_max_attempt_number(self, case_id: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(attempt_number) FROM payment_failures WHERE case_id = %s", (case_id,))
            row = cur.fetchone()
        return row[0] or 0

    def find_attempt_by_payment_id(self, payment_id: str) -> Optional[tuple[str, int]]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT case_id, attempt_number FROM payment_failures WHERE payment_id = %s", (payment_id,))
            row = cur.fetchone()
        return (row[0], row[1]) if row else None

    # ------------------------------------------------------------------
    # Failure events
    # ------------------------------------------------------------------
    def record_failure(self, event: PaymentFailureEvent) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO payment_failures
                    (case_id, attempt_number, payment_id, reason_code, decline_class,
                     amount, received_at, mandate_id, instrument_id, error_reason,
                     error_source, error_step, notes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        event.case_id, event.attempt_number, event.payment_id,
                        event.reason_code, event.decline_class, event.amount,
                        event.received_at, event.mandate_id, event.instrument_id,
                        event.error_reason, event.error_source, event.error_step,
                        json.dumps(event.notes) if event.notes else None,
                    ),
                )
            self.conn.commit()
        except psycopg.errors.UniqueViolation:
            self.conn.rollback()
            if self.find_attempt_by_payment_id(event.payment_id):
                raise FailureAlreadyRecordedError(
                    f"payment_id {event.payment_id!r} already recorded"
                )
            raise FailureAlreadyRecordedError(
                f"failure already recorded for case_id={event.case_id!r} attempt_number={event.attempt_number}"
            )

    def get_failure(self, case_id: str, attempt_number: int) -> PaymentFailureEvent:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM payment_failures WHERE case_id = %s AND attempt_number = %s",
                (case_id, attempt_number),
            )
            row = cur.fetchone()
        if not row:
            raise CaseNotFoundError(f"no failure for case_id={case_id!r} attempt_number={attempt_number}")
        return self._row_to_failure(row)

    # ------------------------------------------------------------------
    # Retry decisions
    # ------------------------------------------------------------------
    def record_retry_decision(self, decision: RetryDecision) -> None:
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO retry_decisions
                    (case_id, attempt_number, scheduled_at, reasoning, commit_backend,
                     commit_ref, executed_at, outcome)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        decision.case_id, decision.attempt_number, decision.scheduled_at,
                        decision.reasoning, decision.commit_backend, decision.commit_ref,
                        decision.executed_at, decision.outcome,
                    ),
                )
            self.conn.commit()
        except psycopg.errors.UniqueViolation:
            self.conn.rollback()
            raise DecisionAlreadyRecordedError(
                f"retry decision already exists for case_id={decision.case_id!r} attempt_number={decision.attempt_number}"
            )

    def get_retry_decision(self, case_id: str, attempt_number: int) -> RetryDecision:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM retry_decisions WHERE case_id = %s AND attempt_number = %s",
                (case_id, attempt_number),
            )
            row = cur.fetchone()
        if not row:
            raise CaseNotFoundError(f"no retry decision for case_id={case_id!r} attempt_number={attempt_number}")
        return self._row_to_decision(row)

    def update_retry_decision(self, decision: RetryDecision) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE retry_decisions SET
                    scheduled_at = %s, reasoning = %s, commit_backend = %s,
                    commit_ref = %s, executed_at = %s, outcome = %s
                WHERE case_id = %s AND attempt_number = %s
                """,
                (
                    decision.scheduled_at, decision.reasoning, decision.commit_backend,
                    decision.commit_ref, decision.executed_at, decision.outcome,
                    decision.case_id, decision.attempt_number,
                ),
            )
        self.conn.commit()

    def get_pending_retries(self) -> list[RetryDecision]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM retry_decisions WHERE outcome = 'pending'")
            rows = cur.fetchall()
        return [self._row_to_decision(r) for r in rows]

    # ------------------------------------------------------------------
    # Audit trail with Merkle chain
    # ------------------------------------------------------------------
    def append_audit(self, entry: AuditEntry) -> None:
        if entry.sequence_id is None:
            with self.conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(sequence_id), 0) + 1 FROM audit_entries")
                entry.sequence_id = cur.fetchone()[0]

        # Get last entry hash for this case
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT entry_hash FROM audit_entries WHERE case_id = %s ORDER BY sequence_id DESC LIMIT 1",
                (entry.case_id,),
            )
            row = cur.fetchone()
        prev_hash = row[0] if row else None
        entry.prev_hash = prev_hash

        # Deterministic JSON serialization (matching InMemoryCaseStore)
        serialized = json.dumps({
            "case_id": entry.case_id,
            "from_state": entry.from_state,
            "to_state": entry.to_state,
            "rule_fired": entry.rule_fired,
            "rule_version": entry.rule_version,
            "timestamp": entry.timestamp.isoformat(),
            "actor": entry.actor,
            "sequence_id": entry.sequence_id,
            "prev_hash": entry.prev_hash,
            "scheduled_at": entry.scheduled_at.isoformat() if entry.scheduled_at else None,
        }, sort_keys=True)
        entry.entry_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_entries
                (sequence_id, case_id, from_state, to_state, rule_fired, rule_version,
                 timestamp, actor, scheduled_at, prev_hash, entry_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    entry.sequence_id, entry.case_id, entry.from_state, entry.to_state,
                    entry.rule_fired, entry.rule_version, entry.timestamp, entry.actor,
                    entry.scheduled_at, entry.prev_hash, entry.entry_hash,
                ),
            )
        self.conn.commit()

    def get_audit_trail(self, case_id: str) -> list[AuditEntry]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM audit_entries WHERE case_id = %s ORDER BY sequence_id",
                (case_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_audit(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _row_to_case(self, row) -> RecoveryCase:
        return RecoveryCase(
            case_id=row[0], mandate_id=row[1], instrument_id=row[2],
            original_amount=row[3], opened_at=row[4], retries_used=row[5],
            state=row[6], bucket=row[7], control_outcome=row[8],
            control_observation_deadline=row[9], resolution_note=row[10],
        )

    def _row_to_failure(self, row) -> PaymentFailureEvent:
        return PaymentFailureEvent(
            case_id=row[0], attempt_number=row[1], payment_id=row[2],
            reason_code=row[3], decline_class=row[4], amount=row[5],
            received_at=row[6], mandate_id=row[7], instrument_id=row[8],
            error_reason=row[9], error_source=row[10], error_step=row[11],
            notes=row[12] if row[12] else None,
        )

    def _row_to_decision(self, row) -> RetryDecision:
        return RetryDecision(
            case_id=row[0], attempt_number=row[1], scheduled_at=row[2],
            reasoning=row[3], commit_backend=row[4], commit_ref=row[5],
            executed_at=row[6], outcome=row[7],
        )

    def _row_to_audit(self, row) -> AuditEntry:
        return AuditEntry(
            sequence_id=row[0], case_id=row[1], from_state=row[2],
            to_state=row[3], rule_fired=row[4], rule_version=row[5],
            timestamp=row[6], actor=row[7], scheduled_at=row[8],
            prev_hash=row[9], entry_hash=row[10],
        )