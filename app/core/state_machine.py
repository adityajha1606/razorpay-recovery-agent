"""
Deterministic finite state machine — builder doc §5.

This is a pure FSM. It receives events (payment.failed, payment.captured)
and explicit transition triggers (notice sent, retry executed, throttle)
and returns the updated case, audit entries, and optionally a RetryDecision
to schedule. It uses only the injected Clock for time and never mutates
global state.

Important:
- `retries_used` increments **only** on `RETRY_EXECUTED -> RETRY_EVAL`
  (i.e., when a retry execution has a negative outcome). Throttle cycles
  never change it (Invariant 8).
- Bucket assignment is deterministic and immutable (Invariant 7).
- All transitions generate AuditEntry records with rule citations.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.core.clock import Clock
from app.core.config import AppConfig
from app.core.decline_router import classify_failure
from app.models import (
    AuditEntry,
    PaymentFailureEvent,
    PaymentSuccessEvent,
    RecoveryCase,
    RetryDecision,
    assign_bucket,
)

RULE_VERSION = 2
SALARY_WINDOW_START_DAY = 1
SALARY_WINDOW_END_DAY = 7
IST = ZoneInfo("Asia/Kolkata")


def _sha256_int(s: str) -> int:
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest(), 16)


def is_control_case(case_id: str, control_percent: int = 20) -> bool:
    return _sha256_int(case_id) % 100 < control_percent


class StateMachine:
    """Pure finite state machine. No instance state."""

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def handle_payment_failed(
        self,
        case: Optional[RecoveryCase],
        event: PaymentFailureEvent,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], Optional[RetryDecision]]:
        if case is None:
            return self._handle_new_failure(event, config, clock)
        else:
            return self._handle_existing_failure(case, event, config, clock)

    def handle_payment_success(
        self,
        case: RecoveryCase,
        event: PaymentSuccessEvent,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], Optional[RetryDecision]]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.bucket == "control":
            if case.state != "CONTROL_HELD":
                audit.append(self._audit(
                    case.case_id, case.state, case.state,
                    "unexpected_success_event", RULE_VERSION, now, "agent",
                ))
                return case, audit, None
            case.state = "CONTROL_RECOVERED"
            case.control_outcome = "recovered_naturally"
            audit.append(self._audit(
                case.case_id, "CONTROL_HELD", "CONTROL_RECOVERED",
                "natural_recovery", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        # treatment bucket
        if case.state == "RETRY_EXECUTED":
            case.state = "RECOVERED"
            audit.append(self._audit(
                case.case_id, "RETRY_EXECUTED", "RECOVERED",
                "agent_recovered", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        if case.state in ("NOTICE_PENDING", "NOTICE_SENT", "RETRY_SCHEDULED", "THROTTLED"):
            from_state = case.state
            case.state = "RECOVERED_NATURALLY"
            audit.append(self._audit(
                case.case_id, from_state, "RECOVERED_NATURALLY",
                "natural_recovery_treatment_bucket", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        audit.append(self._audit(
            case.case_id, case.state, case.state,
            "unexpected_success_event", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    # ------------------------------------------------------------------
    # New failure handling
    # ------------------------------------------------------------------
    def _handle_new_failure(
        self,
        event: PaymentFailureEvent,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], Optional[RetryDecision]]:
        now = clock.now()
        audit: list[AuditEntry] = []

        case = RecoveryCase(
            case_id=event.case_id,
            mandate_id=event.mandate_id or "",
            instrument_id=event.instrument_id,
            original_amount=event.amount,
            opened_at=now,
            state="RECEIVED",
        )

        audit.append(self._audit(
            case.case_id, "RECEIVED", "CLASSIFIED",
            "create_case", RULE_VERSION, now, "agent",
        ))

        decline_class, _ = classify_failure(config, event.reason_code, event.error_reason)
        event.decline_class = decline_class

        if is_control_case(case.case_id):
            assign_bucket(case, "control")
            case.state = "CONTROL_HELD"
            case.control_outcome = "unknown"
            control_window = config.npci_rules.control_observation_window
            case.control_observation_deadline = now + clock.resolve_delay(control_window)
            audit.append(self._audit(
                case.case_id, "CLASSIFIED", "CONTROL_HELD",
                "assign_bucket_control", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        assign_bucket(case, "treatment")
        audit.append(self._audit(
            case.case_id, "CLASSIFIED", "TREATMENT",
            "assign_bucket_treatment", RULE_VERSION, now, "agent",
        ))

        if decline_class == "business":
            case.state = "ESCALATED"
            audit.append(self._audit(
                case.case_id, "TREATMENT", "ESCALATED",
                "hard_decline", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        # AFA threshold check
        if case.original_amount > config.npci_rules.afa_free_ceiling:
            case.state = "AFA_REQUIRED"
            audit.append(self._audit(
                case.case_id, "TREATMENT", "AFA_REQUIRED",
                "afa_threshold_exceeded", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        case.state = "NOTICE_PENDING"
        audit.append(self._audit(
            case.case_id, "TREATMENT", "NOTICE_PENDING",
            "technical_decline", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    # ------------------------------------------------------------------
    # Existing failure handling (control signal or retry outcome)
    # ------------------------------------------------------------------
    def _handle_existing_failure(
        self,
        case: RecoveryCase,
        event: PaymentFailureEvent,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], Optional[RetryDecision]]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.state == "CONTROL_HELD":
            case.state = "CONTROL_STILL_FAILED"
            case.control_outcome = "still_failed"
            audit.append(self._audit(
                case.case_id, "CONTROL_HELD", "CONTROL_STILL_FAILED",
                "control_failure_signal", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        if case.state != "RETRY_EXECUTED":
            audit.append(self._audit(
                case.case_id, case.state, case.state,
                "unexpected_failure_event", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        case.retries_used += 1
        event.decline_class, _ = classify_failure(config, event.reason_code, event.error_reason)

        audit.append(self._audit(
            case.case_id, "RETRY_EXECUTED", "RETRY_EVAL",
            "execution_outcome", RULE_VERSION, now, "agent",
        ))

        if (case.retries_used >= config.npci_rules.max_retries or
                event.decline_class == "business"):
            case.state = "ESCALATED"
            audit.append(self._audit(
                case.case_id, "RETRY_EVAL", "ESCALATED",
                "retry_cap_or_business", RULE_VERSION, now, "agent",
            ))
            return case, audit, None

        case.state = "NOTICE_PENDING"
        audit.append(self._audit(
            case.case_id, "RETRY_EVAL", "NOTICE_PENDING",
            "technical_decline_retry_eligible", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    # ------------------------------------------------------------------
    # Explicit transition triggers (called by services)
    # ------------------------------------------------------------------
    def mark_notice_sent(
        self,
        case: RecoveryCase,
        config: AppConfig,
        clock: Clock,
        reason_code: Optional[str] = None,
        previous_executed_at: Optional[datetime] = None,
    ) -> tuple[RecoveryCase, list[AuditEntry], Optional[RetryDecision]]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.state != "NOTICE_PENDING":
            raise ValueError(f"Cannot send notice from state {case.state}")

        case.state = "NOTICE_SENT"
        audit.append(self._audit(
            case.case_id, "NOTICE_PENDING", "NOTICE_SENT",
            "notice_sent", RULE_VERSION, now, "agent",
        ))

        scheduled_at, reasoning = self._compute_scheduled_at(
            case.retries_used, reason_code, config, clock, previous_executed_at,
        )

        decision = RetryDecision(
            case_id=case.case_id,
            attempt_number=case.retries_used + 1,
            scheduled_at=scheduled_at,
            reasoning=reasoning,
            commit_backend="postgres_outbox",
            commit_ref=f"retry-{case.case_id}-{case.retries_used + 1}",
        )

        case.state = "RETRY_SCHEDULED"
        audit.append(self._audit(
            case.case_id, "NOTICE_SENT", "RETRY_SCHEDULED",
            "schedule_retry", RULE_VERSION, now, "agent",
        ))
        # Attach scheduled_at for independent verification
        audit[-1].scheduled_at = decision.scheduled_at

        return case, audit, decision

    def mark_retry_executed(
        self,
        case: RecoveryCase,
        decision: RetryDecision,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], None]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.state != "RETRY_SCHEDULED":
            raise ValueError(f"Cannot execute retry from state {case.state}")

        case.state = "RETRY_EXECUTED"
        decision.executed_at = now
        audit.append(self._audit(
            case.case_id, "RETRY_SCHEDULED", "RETRY_EXECUTED",
            "commit_approved", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    def attempt_execution(
        self,
        case: RecoveryCase,
        decision: RetryDecision,
        commit_backend,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], bool]:
        if case.state != "RETRY_SCHEDULED":
            raise ValueError(f"Cannot attempt execution from state {case.state}")

        committed = commit_backend.commit(decision)
        if not committed:
            return case, [], False

        now = clock.now()
        audit: list[AuditEntry] = []
        case.state = "RETRY_EXECUTED"
        decision.executed_at = now
        audit.append(self._audit(
            case.case_id, "RETRY_SCHEDULED", "RETRY_EXECUTED",
            "commit_approved", RULE_VERSION, now, "agent",
        ))
        return case, audit, True

    def apply_throttle(
        self,
        case: RecoveryCase,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], None]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.state != "RETRY_SCHEDULED":
            raise ValueError(f"Cannot throttle from state {case.state}")

        case.state = "THROTTLED"
        audit.append(self._audit(
            case.case_id, "RETRY_SCHEDULED", "THROTTLED",
            "throttle_applied", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    def release_throttle(
        self,
        case: RecoveryCase,
        config: AppConfig,
        clock: Clock,
    ) -> tuple[RecoveryCase, list[AuditEntry], None]:
        now = clock.now()
        audit: list[AuditEntry] = []

        if case.state != "THROTTLED":
            raise ValueError(f"Cannot release throttle from state {case.state}")

        case.state = "RETRY_SCHEDULED"
        audit.append(self._audit(
            case.case_id, "THROTTLED", "RETRY_SCHEDULED",
            "throttle_released", RULE_VERSION, now, "agent",
        ))
        return case, audit, None

    # ------------------------------------------------------------------
    # Helper: compute scheduled retry time (§9B)
    # ------------------------------------------------------------------
    def _compute_scheduled_at(
        self,
        retries_used: int,
        reason_code: Optional[str],
        config: AppConfig,
        clock: Clock,
        previous_executed_at: Optional[datetime] = None,
    ) -> tuple[datetime, str]:
        spacing = config.npci_rules.spacing
        idx = min(retries_used, len(spacing) - 1)
        spacing_delay = spacing[idx]
        notice_lead = config.npci_rules.notice_lead_time

        now = clock.now()
        by_notice = now + notice_lead
        if previous_executed_at is not None:
            by_spacing = previous_executed_at + spacing_delay
        else:
            by_spacing = now + spacing_delay

        floor_at = max(by_notice, by_spacing)
        floor_delay = floor_at - now
        constraint_note = (
            "notice lead" if by_notice >= by_spacing else "NPCI spacing floor"
        )

        if reason_code == "insufficient_funds":
            latest_at = floor_at + config.npci_rules.max_schedule_window
            salary_day = self._first_salary_window_day(floor_at, latest_at)
            if salary_day is not None:
                chosen_delay = salary_day - now
                scheduled_at = now + clock.resolve_delay(chosen_delay)
                reasoning = (
                    f"Retry attempt {retries_used + 1}: insufficient_funds bias applied — "
                    f"targeting {salary_day.date()} (day-{SALARY_WINDOW_START_DAY}"
                    f"\u2013{SALARY_WINDOW_END_DAY} salary window), no earlier than the "
                    f"{constraint_note} floor (config v{RULE_VERSION})."
                )
                scheduled_at = self._avoid_peak_windows(scheduled_at, config, clock, now)
                reasoning += " Peak-hour blackout applied."
                return scheduled_at, reasoning

        scheduled_at = now + clock.resolve_delay(floor_delay)
        reasoning = (
            f"Retry attempt {retries_used + 1}: scheduled at the {constraint_note} floor "
            f"(config v{RULE_VERSION})."
        )
        scheduled_at = self._avoid_peak_windows(scheduled_at, config, clock, now)
        if scheduled_at != now + clock.resolve_delay(floor_delay):
            reasoning += " Peak-hour blackout applied."
        return scheduled_at, reasoning

    def _avoid_peak_windows(
        self,
        scheduled_at: datetime,
        config: AppConfig,
        clock: Clock,
        now: datetime,
    ) -> datetime:
        scheduled_ist = scheduled_at.astimezone(IST)
        for window in config.npci_rules.peak_windows:
            current_time = scheduled_ist.time()
            if window.start <= current_time < window.end:
                adjusted_ist = scheduled_ist.replace(
                    hour=window.end.hour,
                    minute=window.end.minute,
                    second=0,
                    microsecond=0,
                )
                return adjusted_ist.astimezone(timezone.utc)
        return scheduled_at

    @staticmethod
    def _first_salary_window_day(
        window_start: datetime, window_end: datetime
    ) -> Optional[datetime]:
        if window_end < window_start:
            return None
        cursor = window_start
        while cursor <= window_end:
            if SALARY_WINDOW_START_DAY <= cursor.day <= SALARY_WINDOW_END_DAY:
                return cursor
            cursor += timedelta(days=1)
        return None

    def _audit(
        self,
        case_id: str,
        from_state: str,
        to_state: str,
        rule_fired: str,
        rule_version: int,
        timestamp: datetime,
        actor: str,
    ) -> AuditEntry:
        return AuditEntry(
            case_id=case_id,
            from_state=from_state,
            to_state=to_state,
            rule_fired=rule_fired,
            rule_version=rule_version,
            timestamp=timestamp,
            actor=actor,  # type: ignore[arg-type]
        )