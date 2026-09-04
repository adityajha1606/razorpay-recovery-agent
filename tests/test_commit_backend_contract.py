"""Contract tests for CommitBackend implementations."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.commit_backend import EtcdQuorumBackend, PostgresOutboxBackend
from app.models import RetryDecision


def make_decision(case_id: str = "pay_abc123", attempt: int = 1) -> RetryDecision:
    return RetryDecision(
        case_id=case_id,
        attempt_number=attempt,
        scheduled_at=datetime.now(timezone.utc),
        reasoning="earliest legal slot",
        commit_backend="postgres_outbox",
        commit_ref=f"ref-{case_id}-{attempt}",
    )


# ---------------------------------------------------------------------------
# Fake etcd client
# ---------------------------------------------------------------------------
class FakeEtcdClient:
    """Minimal fake matching how EtcdQuorumBackend uses the etcd3 client."""

    def __init__(self):
        self.committed_keys = set()
        self.transactions = self

    def version(self, key: str):
        return 0 if key not in self.committed_keys else 1

    def put(self, key: str, value: str):
        # In real etcd3, put returns a Put object; our fake just returns the key.
        return key

    def transaction(self, compare, success, failure):
        # The backend passes compare=[version(key)==0] and success=[put(key, ref)].
        # Since our fake put returns the key string, success[0] is the key.
        key = success[0]  # string path
        if key in self.committed_keys:
            return False, []
        self.committed_keys.add(key)
        return True, [1]


# ---------------------------------------------------------------------------
# Fake Postgres connection/cursor
# ---------------------------------------------------------------------------
class UniqueViolation(Exception):
    """A fake unique-violation exception with the psycopg sqlstate attribute."""
    def __init__(self):
        super().__init__("duplicate key")
        self.diag = type("Diag", (), {"sqlstate": "23505"})()


class FakeCursor:
    def __init__(self, shared_rows):
        self.shared_rows = shared_rows
        self.executed = False

    def execute(self, query, params):
        case_id, attempt = params[0], params[1]
        if (case_id, attempt) in self.shared_rows:
            raise UniqueViolation()
        self.shared_rows.add((case_id, attempt))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeConnection:
    def __init__(self):
        self.shared_rows = set()
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return FakeCursor(self.shared_rows)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------
def test_etcd_first_commit_true():
    client = FakeEtcdClient()
    backend = EtcdQuorumBackend(client)
    decision = make_decision()
    assert backend.commit(decision) is True


def test_etcd_second_commit_false():
    client = FakeEtcdClient()
    backend = EtcdQuorumBackend(client)
    decision = make_decision()
    assert backend.commit(decision) is True
    assert backend.commit(decision) is False


def test_etcd_different_attempt_true():
    client = FakeEtcdClient()
    backend = EtcdQuorumBackend(client)
    d1 = make_decision(attempt=1)
    d2 = make_decision(attempt=2)
    assert backend.commit(d1) is True
    assert backend.commit(d2) is True


def test_postgres_first_commit_true():
    conn = FakeConnection()
    backend = PostgresOutboxBackend(conn)
    decision = make_decision()
    assert backend.commit(decision) is True


def test_postgres_second_commit_false():
    conn = FakeConnection()
    backend = PostgresOutboxBackend(conn)
    decision = make_decision()
    assert backend.commit(decision) is True
    assert backend.commit(decision) is False


def test_postgres_different_attempt_true():
    conn = FakeConnection()
    backend = PostgresOutboxBackend(conn)
    d1 = make_decision(attempt=1)
    d2 = make_decision(attempt=2)
    assert backend.commit(d1) is True
    assert backend.commit(d2) is True