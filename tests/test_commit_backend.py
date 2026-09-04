"""
Unit tests for app/core/commit_backend.py (builder doc §10.1).

Both backends take their client/connection via dependency injection, so we
exercise the idempotency contract — the thing that actually matters for
Invariant 4 — with small fakes instead of a live etcd cluster or Postgres.
This is exactly what that design choice buys: these tests run with zero
infra and zero extra dependencies installed.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.commit_backend import EtcdQuorumBackend, PostgresOutboxBackend
from app.models import RetryDecision

NOW = datetime.now(timezone.utc)


def make_decision(attempt_number: int = 1, commit_backend: str = "etcd") -> RetryDecision:
    return RetryDecision(
        case_id="pay_abc123",
        attempt_number=attempt_number,
        scheduled_at=NOW,
        reasoning="earliest legal slot per §9B",
        commit_backend=commit_backend,  # type: ignore[arg-type]
        commit_ref=f"pay_abc123-{attempt_number}",
    )


# ---------------------------------------------------------------------------
# Fake etcd3 client — just enough of the real transaction API (confirmed
# against python-etcd3's documented usage) to exercise our compare/success
# logic: `transactions.version(key) == 0` as the compare, `transactions.put`
# as the success op.
# ---------------------------------------------------------------------------


class _FakeVersionComparison:
    def __init__(self, key: str, expected: int) -> None:
        self.key = key
        self.expected = expected

    def evaluate(self, store: dict[str, str]) -> bool:
        version = 1 if self.key in store else 0
        return version == self.expected


class _FakePutOp:
    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value


class _FakeTransactions:
    def version(self, key: str) -> "_FakeVersionBuilder":
        return _FakeVersionBuilder(key)

    def put(self, key: str, value: str) -> _FakePutOp:
        return _FakePutOp(key, value)


class _FakeVersionBuilder:
    def __init__(self, key: str) -> None:
        self.key = key

    def __eq__(self, other: object) -> _FakeVersionComparison:  # type: ignore[override]
        assert isinstance(other, int)
        return _FakeVersionComparison(self.key, other)


class FakeEtcdClient:
    """Enough of `etcd3.Etcd3Client` to test EtcdQuorumBackend's idempotency
    logic: a single-key put/version store plus a transaction() that only
    applies its success ops when every compare passes."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.transactions = _FakeTransactions()

    def transaction(self, compare, success, failure):
        ok = all(c.evaluate(self.store) for c in compare)
        for op in success if ok else failure:
            if isinstance(op, _FakePutOp):
                self.store[op.key] = op.value
        return ok, []


class TestEtcdQuorumBackend:
    def test_first_commit_succeeds(self) -> None:
        backend = EtcdQuorumBackend(client=FakeEtcdClient())
        assert backend.commit(make_decision()) is True

    def test_duplicate_commit_for_same_attempt_fails_closed(self) -> None:
        client = FakeEtcdClient()
        backend = EtcdQuorumBackend(client=client)
        decision = make_decision(attempt_number=2)

        assert backend.commit(decision) is True
        assert backend.commit(decision) is False  # Invariant 4

    def test_different_attempts_on_same_case_both_succeed(self) -> None:
        client = FakeEtcdClient()
        backend = EtcdQuorumBackend(client=client)

        assert backend.commit(make_decision(attempt_number=1)) is True
        assert backend.commit(make_decision(attempt_number=2)) is True


# ---------------------------------------------------------------------------
# Fake psycopg-shaped connection — enough to test PostgresOutboxBackend's
# idempotency logic without a live database. Mirrors the "commit vs.
# rollback stages writes" semantics of a real Postgres transaction.
# ---------------------------------------------------------------------------


class _FakeIntegrityError(Exception):
    class _Diag:
        sqlstate = "23505"

    diag = _Diag()


class _FakeCursor:
    def __init__(self, conn: "FakeConnection") -> None:
        self._conn = conn

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple) -> None:
        case_id, attempt_number, commit_ref = params
        key = (case_id, attempt_number)
        if key in self._conn.committed_rows:
            raise _FakeIntegrityError(
                f"duplicate key value violates unique constraint on {key}"
            )
        self._conn.pending_rows[key] = commit_ref


class FakeConnection:
    def __init__(self) -> None:
        self.committed_rows: dict[tuple[str, int], str] = {}
        self.pending_rows: dict[tuple[str, int], str] = {}

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.committed_rows.update(self.pending_rows)
        self.pending_rows.clear()

    def rollback(self) -> None:
        self.pending_rows.clear()


class TestPostgresOutboxBackend:
    def test_first_commit_succeeds(self) -> None:
        backend = PostgresOutboxBackend(connection=FakeConnection())
        assert backend.commit(make_decision(commit_backend="postgres_outbox")) is True

    def test_duplicate_commit_for_same_attempt_fails_closed(self) -> None:
        connection = FakeConnection()
        backend = PostgresOutboxBackend(connection=connection)
        decision = make_decision(attempt_number=3, commit_backend="postgres_outbox")

        assert backend.commit(decision) is True
        assert backend.commit(decision) is False  # Invariant 4
        # and the failed second attempt must not have left a half-applied row
        assert connection.pending_rows == {}

    def test_different_attempts_on_same_case_both_succeed(self) -> None:
        connection = FakeConnection()
        backend = PostgresOutboxBackend(connection=connection)

        assert backend.commit(make_decision(attempt_number=1, commit_backend="postgres_outbox")) is True
        assert backend.commit(make_decision(attempt_number=2, commit_backend="postgres_outbox")) is True
