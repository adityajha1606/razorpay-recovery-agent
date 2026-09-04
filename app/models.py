"""
Data models — builder doc §6, the shared contract across every worktree.

These are plain dataclasses, not pydantic models: nothing here needs FastAPI
request/response validation, and keeping this module dependency-free (stdlib
only) means it can be imported and unit-tested without the web/db/etcd stack
installed. Field names and types mirror §6 exactly — if you need a field
that isn't here, that's a builder-doc conversation, not a quiet addition.

Defensive validation lives in `__post_init__` for values that can never be
legal (negative money, negative counters) — it deliberately does NOT
duplicate business rules that belong in the state machine (e.g. "attempt 4
is not allowed"), since encoding those here would let two different pieces
of code disagree about what's legal. Bucket immutability (Invariant 7) is
enforced by `assign_bucket()` below rather than by direct field assignment,
so there's exactly one place that guarantee can be violated and it's easy
to audit.

Monetary values are stored as integer paise (1 rupee = 100 paise), never as
floats or Decimal rupees. This is the SSS‑grade rule for financial integrity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

Bucket = Literal["treatment", "control"]
DeclineClass = Literal["technical", "business", "unclassified"]
ControlOutcome = Literal["recovered_naturally", "still_failed", "unknown"]
ResolutionNote = Literal["written_off", "recovered_manually"]
CommitBackendName = Literal["etcd", "postgres_outbox"]
RetryOutcome = Literal["pending", "recovered", "failed"]
Actor = Literal["agent", "human"]


@dataclass
class RecoveryCase:
    """The stable anchor for one recovery effort. `case_id` is the first
    failed `payment_id` — see §14 Q9 on case vs. attempt identity.

    `original_amount` is in integer paise (e.g., ₹499.00 = 49900 paise).
    """

    case_id: str
    mandate_id: str
    instrument_id: str  # hashed VPA/account — never store raw, see §15
    original_amount: int  # integer paise
    opened_at: datetime
    retries_used: int = 0
    state: str = "RECEIVED"
    bucket: Optional[Bucket] = None  # set once via assign_bucket() — see Invariant 7
    control_outcome: Optional[ControlOutcome] = None  # control-bucket only
    control_observation_deadline: Optional[datetime] = None  # control-bucket only
    resolution_note: Optional[ResolutionNote] = None  # set on human resolution

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must not be empty")
        if not self.mandate_id:
            raise ValueError("mandate_id must not be empty")
        if not self.instrument_id:
            raise ValueError("instrument_id must not be empty")
        if self.original_amount <= 0:
            raise ValueError(
                f"original_amount must be positive integer paise, got {self.original_amount}"
            )
        if self.retries_used < 0:
            raise ValueError(f"retries_used cannot be negative, got {self.retries_used}")


def assign_bucket(case: RecoveryCase, bucket: Bucket) -> None:
    """Set `case.bucket`, enforcing Invariant 7 (builder doc §8.7): once set
    at first classification, bucket never changes for the life of a case.

    This is the ONLY sanctioned way to set `RecoveryCase.bucket` — don't
    assign the field directly, or this guarantee has no single enforcement
    point left to test or audit.
    """
    if case.bucket is not None and case.bucket != bucket:
        raise ValueError(
            f"bucket is immutable once set (Invariant 7): cannot change "
            f"case {case.case_id!r} from {case.bucket!r} to {bucket!r}"
        )
    case.bucket = bucket


@dataclass
class PaymentFailureEvent:
    """One per attempt. Razorpay issues a NEW payment_id per retry (§6);
    `case_id` is the stable anchor (§14 Q9). Fields mirror the real
    `payment.failed` payload from Razorpay docs, with additions for
    mandate mapping and raw error details.

    `amount` is integer paise (e.g., ₹500.00 = 50000 paise).

    `mandate_id` and `instrument_id` are *extraction hints* used only when
    opening or matching a case. The canonical source of truth for a case's
    mandate/instrument is `RecoveryCase` — never read these from the event
    downstream.
    """

    case_id: str
    payment_id: str
    attempt_number: int
    reason_code: str
    decline_class: DeclineClass
    amount: int  # integer paise, never rupees
    received_at: datetime
    mandate_id: Optional[str] = None      # extraction hint (see docstring)
    instrument_id: str = ""               # extraction hint (see docstring)
    error_reason: Optional[str] = None
    error_source: Optional[str] = None
    error_step: Optional[str] = None
    notes: Optional[dict] = None          # raw notes dict, may contain mandate_id

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError(f"attempt_number must be >= 1, got {self.attempt_number}")
        if self.amount <= 0:
            raise ValueError(f"amount must be positive integer paise, got {self.amount}")
        if not self.payment_id:
            raise ValueError("payment_id must not be empty")
        if not self.case_id:
            raise ValueError("case_id must not be empty")


@dataclass
class PaymentSuccessEvent:
    """One per successful capture, from Razorpay's `payment.captured` webhook.

    `amount` is integer paise. `matched_attempt_number` is left for the
    state machine to set — matching payment_id against outstanding
    RetryDecision.commit_ref values is business logic, not parsing.
    """

    case_id: str
    payment_id: str
    amount: int
    captured_at: datetime
    matched_attempt_number: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must not be empty")
        if not self.payment_id:
            raise ValueError("payment_id must not be empty")
        if self.amount <= 0:
            raise ValueError(f"amount must be positive integer paise, got {self.amount}")


@dataclass
class RetryDecision:
    """One scheduled (and eventually executed) retry attempt, with the
    reasoning string the rule-citation receipt (§9F) renders verbatim."""

    case_id: str
    attempt_number: int
    scheduled_at: datetime
    reasoning: str
    commit_backend: CommitBackendName
    commit_ref: str
    executed_at: Optional[datetime] = None
    outcome: RetryOutcome = "pending"

    def __post_init__(self) -> None:
        if self.attempt_number < 1:
            raise ValueError(f"attempt_number must be >= 1, got {self.attempt_number}")
        if not self.reasoning:
            raise ValueError("reasoning must not be empty — the receipt has nothing to show")
        if not self.commit_ref:
            raise ValueError("commit_ref must not be empty")


@dataclass
class AuditEntry:
    """Append-only audit log row. `rule_fired` + `rule_version` are what the
    rule-citation receipt (§9F) actually renders — every state transition
    must produce one of these naming the exact rule that caused it.

    `sequence_id` is optional; the store assigns it if None.
    """

    case_id: str
    from_state: str
    to_state: str
    rule_fired: str
    rule_version: int
    timestamp: datetime
    actor: Actor
    sequence_id: Optional[int] = None  # assigned by store if None

    def __post_init__(self) -> None:
        if not self.rule_fired:
            raise ValueError("rule_fired must not be empty — receipts must cite a real rule")
        if self.rule_version < 1:
            raise ValueError(f"rule_version must be >= 1, got {self.rule_version}")


@dataclass
class NoticeRecord:
    """One batched pre-debit notice per instrument per day (§9E)."""

    instrument_id: str
    mandate_ids: list[str] = field(default_factory=list)
    sent_at: Optional[datetime] = None
    dnd_deferred: bool = False

    def __post_init__(self) -> None:
        if not self.mandate_ids:
            raise ValueError("mandate_ids must not be empty — a notice needs at least one mandate")