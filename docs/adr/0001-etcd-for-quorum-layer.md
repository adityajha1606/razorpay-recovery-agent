# 0001. etcd for the quorum layer

Status: Accepted
Date: hour 0
Builder doc reference: §3, §10.1, §14 Q1

## Decision
Use `etcd` (via the `etcd3` Python client, 3-node docker-compose) as the
quorum backend for the execute-approval decision, with `PostgresOutboxBackend`
as a same-contract fallback behind the `CommitBackend` protocol (§10.1).

## Why
Raw Raft and CockroachDB were considered. etcd wins on a legible
leader/follower story that's easy to demo (kill the leader, watch a new one
get elected) without extra operational surface. The known risk is the
`etcd3` client itself being fiddly to get demo-reliable in Docker — that risk
is exactly why §10.1 specifies a same-idempotency-contract fallback from the
start, not as a reaction to trouble.

## Consequences
- We accept real integration risk with `etcd3` and budget an explicit hour-0
  spike (`spikes/etcd_spike.py`) plus a hard gate at hour 20 to de-risk it.
- If the gate isn't met, `PostgresOutboxBackend` takes over with identical
  guarantees for the one decision this layer protects — see ADR 0003 for the
  exact scope of what "protects" means here.
- This decision is about the quorum layer only. It says nothing about where
  `RecoveryCase` state or audit history live (they don't live in etcd) —
  see ADR 0003.
