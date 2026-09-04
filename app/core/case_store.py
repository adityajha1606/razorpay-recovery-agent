"""
In‑memory case store — Phase 1 single‑writer repository.

The state machine reads and writes through this store, never directly to
dicts. It keeps every entity keyed by case_id and (case_id, attempt_number)
so the invariants can be enforced in one place. This is deliberately not
thread‑safe: FastAPI runs async but our state machine is single‑threaded,
and the builder doc §4 says "Deterministic Python, single‑threaded apply
loop". All methods are synchronous — no `await`, no I/O, no hidden state.

The audit trail is Merkle‑chained: each entry's hash includes the previous
entry's hash, making the log tamper‑evident.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

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


@dataclass
class InMemoryCaseStore:
    """Simple in‑memory repository. All lookups are by case_id or
    (case_id, attempt_number). Methods are synchronous and deterministic."""

    _cases: dict[str, RecoveryCase] = field(default_factory=dict)
    _failures: dict[tuple[str, int], PaymentFailureEvent] = field(default_factory=dict)
    _decisions: dict[tuple[str, int], RetryDecision] = field(default_factory=dict)
    _audit_logs: dict[str, list[AuditEntry]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _next_sequence: int = 1
    _mandate_index: dict[str, str] = field(default_factory=dict)
    _payment_index: dict[str, str] = field(default_factory=dict)
    _payment_attempt_index: dict[str, tuple[str, int]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Case management
    # ------------------------------------------------------------------
    def create_case(self, case: RecoveryCase) -> None:
        """Store a new RecoveryCase. Raises if case_id already exists or
        if mandate_id is already mapped to a different case."""
        if case.case_id in self._cases:
            raise CaseAlreadyExistsError(f"case_id {case.case_id!r} already exists")

        if case.mandate_id:
            existing_case_id = self._mandate_index.get(case.mandate_id)
            if existing_case_id and existing_case_id != case.case_id:
                raise CaseAlreadyExistsError(
                    f"mandate_id {case.mandate_id!r} already mapped to case_id {existing_case_id!r}"
                )

        self._cases[case.case_id] = case
        if case.mandate_id:
            self._mandate_index[case.mandate_id] = case.case_id
        self._payment_index[case.case_id] = case.case_id

    def get_case(self, case_id: str) -> RecoveryCase:
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise CaseNotFoundError(f"no case with case_id {case_id!r}") from exc

    def update_case(self, case: RecoveryCase) -> None:
        if case.case_id not in self._cases:
            raise CaseNotFoundError(f"cannot update missing case {case.case_id!r}")
        self._cases[case.case_id] = case
        if case.mandate_id:
            self._mandate_index[case.mandate_id] = case.case_id

    def list_cases(self) -> list[RecoveryCase]:
        return list(self._cases.values())

    def find_case_by_mandate(self, mandate_id: str) -> Optional[RecoveryCase]:
        case_id = self._mandate_index.get(mandate_id)
        if not case_id:
            return None
        return self._cases.get(case_id)

    def find_case_by_payment_id(self, payment_id: str) -> Optional[RecoveryCase]:
        case_id = self._payment_index.get(payment_id)
        if not case_id:
            return None
        return self._cases.get(case_id)

    def get_max_attempt_number(self, case_id: str) -> int:
        max_attempt = 0
        for (cid, attempt) in self._failures.keys():
            if cid == case_id:
                max_attempt = max(max_attempt, attempt)
        return max_attempt

    def find_attempt_by_payment_id(self, payment_id: str) -> Optional[tuple[str, int]]:
        return self._payment_attempt_index.get(payment_id)

    # ------------------------------------------------------------------
    # Failure events
    # ------------------------------------------------------------------
    def record_failure(self, event: PaymentFailureEvent) -> None:
        key = (event.case_id, event.attempt_number)
        if key in self._failures:
            raise FailureAlreadyRecordedError(
                f"failure already recorded for case_id={event.case_id!r} "
                f"attempt_number={event.attempt_number}"
            )
        if event.payment_id in self._payment_attempt_index:
            existing_case_id, existing_attempt = self._payment_attempt_index[event.payment_id]
            raise FailureAlreadyRecordedError(
                f"payment_id {event.payment_id!r} already recorded against "
                f"case_id={existing_case_id!r} attempt_number={existing_attempt} — "
                f"this looks like a Razorpay webhook redelivery, not a new attempt"
            )
        self._failures[key] = event
        self._payment_attempt_index[event.payment_id] = (event.case_id, event.attempt_number)

    def get_failure(self, case_id: str, attempt_number: int) -> PaymentFailureEvent:
        key = (case_id, attempt_number)
        try:
            return self._failures[key]
        except KeyError as exc:
            raise CaseNotFoundError(
                f"no failure for case_id={case_id!r} attempt_number={attempt_number}"
            ) from exc

    # ------------------------------------------------------------------
    # Retry decisions
    # ------------------------------------------------------------------
    def record_retry_decision(self, decision: RetryDecision) -> None:
        key = (decision.case_id, decision.attempt_number)
        if key in self._decisions:
            raise DecisionAlreadyRecordedError(
                f"retry decision already exists for case_id={decision.case_id!r} "
                f"attempt_number={decision.attempt_number}"
            )
        self._decisions[key] = decision

    def get_retry_decision(self, case_id: str, attempt_number: int) -> RetryDecision:
        key = (case_id, attempt_number)
        try:
            return self._decisions[key]
        except KeyError as exc:
            raise CaseNotFoundError(
                f"no retry decision for case_id={case_id!r} attempt_number={attempt_number}"
            ) from exc

    def update_retry_decision(self, decision: RetryDecision) -> None:
        key = (decision.case_id, decision.attempt_number)
        if key not in self._decisions:
            raise CaseNotFoundError(
                f"no retry decision for case_id={decision.case_id!r} attempt_number={decision.attempt_number}"
            )
        self._decisions[key] = decision

    def get_pending_retries(self) -> list[RetryDecision]:
        return [d for d in self._decisions.values() if d.outcome == "pending"]

    # ------------------------------------------------------------------
    # Audit trail with Merkle chain
    # ------------------------------------------------------------------
    @staticmethod
    def _serialize_entry(entry: AuditEntry) -> str:
        """Return a deterministic JSON string for hashing an AuditEntry."""
        return json.dumps({
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

    @staticmethod
    def _hash_entry(entry: AuditEntry) -> str:
        """Compute SHA-256 hash of the serialized entry."""
        serialized = InMemoryCaseStore._serialize_entry(entry)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def append_audit(self, entry: AuditEntry) -> None:
        """Append an AuditEntry to the case's audit trail. Assigns
        sequence_id if None, links the previous hash, and stores the entry
        hash so the chain can be verified independently."""
        if entry.sequence_id is None:
            entry.sequence_id = self._next_sequence
            self._next_sequence += 1

        if self._audit_logs.get(entry.case_id):
            last_entry = self._audit_logs[entry.case_id][-1]
            entry.prev_hash = last_entry.entry_hash
        else:
            entry.prev_hash = None

        entry.entry_hash = self._hash_entry(entry)
        self._audit_logs[entry.case_id].append(entry)

    def get_audit_trail(self, case_id: str) -> list[AuditEntry]:
        return list(self._audit_logs.get(case_id, []))