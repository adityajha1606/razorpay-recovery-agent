"""
CommitBackend — builder doc §10.1.

Protects exactly one decision: whether a given execute action is approved
to fire. It does NOT replicate `RecoveryCase` state, audit history, or
anything else — that lives in ordinary Postgres, outside this layer. See
docs/adr/0003-quorum-scope-execute-approval-only.md.

Both implementations take their client/connection as a constructor argument
(dependency injection) rather than importing `etcd3` or a Postgres driver at
module level. This keeps the module importable — and unit-testable with a
fake client — in an environment that doesn't have either library installed,
and means picking a backend is a config change (see docs/adr/0001), not an
import change.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

from app.models import RetryDecision

logger = logging.getLogger(__name__)


@runtime_checkable
class CommitBackend(Protocol):
    def commit(self, action: RetryDecision) -> bool:
        """Attempt to durably commit approval for `action`.

        Returns True if this call is the one that won approval — the first
        commit for this (case_id, attempt_number). Returns False if approval
        was already committed by an earlier call, which is what makes
        Invariant 4 (no (case_id, attempt_number) pair ever executes twice)
        hold even across a leader failover or a retried request.
        """
        ...

    def commit_effect(self, case_id: str, attempt: int, effect: dict) -> bool:
        """Commit a full effect object for exactly-once application.

        Like `commit`, but stores the entire effect payload instead of just
        a boolean. This allows the case store to be rebuilt/idempotently
        healed from the commit log after a crash.
        """
        ...


class EtcdQuorumBackend:
    """Quorum-committed backend (§3). Requires a running etcd cluster
    (`docker compose up etcd`). Hard gate at hour 20 (§10.1): if this isn't
    clean and demo-reliable by then, flip the config to
    `PostgresOutboxBackend` and don't come back to etcd mid-build.
    """

    def __init__(self, client: Any) -> None:
        """`client` is an `etcd3.Etcd3Client` (or anything exposing the same
        `.transaction()` / `.transactions` surface) — injected so this class
        is testable without a live cluster."""
        self._client = client

    def commit(self, action: RetryDecision) -> bool:
        key = self._key_for(action)
        success, _responses = self._client.transaction(
            compare=[self._client.transactions.version(key) == 0],
            success=[self._client.transactions.put(key, action.commit_ref)],
            failure=[],
        )
        if not success:
            logger.warning(
                "etcd commit already exists for case_id=%s attempt_number=%s "
                "— refusing to execute twice (Invariant 4)",
                action.case_id,
                action.attempt_number,
            )
        return bool(success)

    def commit_effect(self, case_id: str, attempt: int, effect: dict) -> bool:
        key = f"/recovery/commits/{case_id}/{attempt}"
        value = json.dumps(effect)
        success, _responses = self._client.transaction(
            compare=[self._client.transactions.version(key) == 0],
            success=[self._client.transactions.put(key, value)],
            failure=[],
        )
        if not success:
            logger.warning(
                "etcd commit already exists for case_id=%s attempt_number=%s "
                "— refusing to execute twice (Invariant 4)",
                case_id,
                attempt,
            )
        return bool(success)

    @staticmethod
    def _key_for(action: RetryDecision) -> str:
        return f"/recovery/commits/{action.case_id}/{action.attempt_number}"


class PostgresOutboxBackend:
    """Single-writer fallback (§10.1). Same idempotency contract as
    `EtcdQuorumBackend`: a unique constraint on (case_id, attempt_number) in
    the outbox table does the same job etcd's transaction does — see
    `schema.sql` for the table definition this relies on.
    """

    def __init__(self, connection: Any) -> None:
        """`connection` is a psycopg `Connection` (or anything exposing the
        same `.cursor()` / `.commit()` / `.rollback()` surface) — injected so
        this class is testable without a live database."""
        self._conn = connection

    def commit(self, action: RetryDecision) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO commit_outbox (case_id, attempt_number, commit_ref)
                    VALUES (%s, %s, %s)
                    """,
                    (action.case_id, action.attempt_number, action.commit_ref),
                )
            self._conn.commit()
            return True
        except Exception as exc:
            self._conn.rollback()
            if _is_unique_violation(exc):
                logger.warning(
                    "outbox commit already exists for case_id=%s attempt_number=%s "
                    "— refusing to execute twice (Invariant 4)",
                    action.case_id,
                    action.attempt_number,
                )
                return False
            logger.error(
                "unexpected outbox commit failure for case_id=%s attempt_number=%s: %s",
                action.case_id,
                action.attempt_number,
                exc,
                exc_info=True,
            )
            raise

    def commit_effect(self, case_id: str, attempt: int, effect: dict) -> bool:
        # For Postgres, we can store the effect in a column; for simplicity,
        # we reuse commit but encode the effect as commit_ref.
        action = RetryDecision(
            case_id=case_id,
            attempt_number=attempt,
            scheduled_at=datetime.now(timezone.utc),
            reasoning="",
            commit_backend="postgres_outbox",
            commit_ref=json.dumps(effect),
        )
        return self.commit(action)


OUTBOX_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS commit_outbox (
    case_id         TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    commit_ref      TEXT NOT NULL,
    committed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (case_id, attempt_number)
);
"""


def _is_unique_violation(exc: Exception) -> bool:
    sqlstate = getattr(getattr(exc, "diag", None), "sqlstate", None)
    return sqlstate == "23505"