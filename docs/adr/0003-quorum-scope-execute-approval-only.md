# 0003. Quorum scope is execute-approval only

Status: Accepted
Date: hour 0
Builder doc reference: §4, §10.3, §14 Q17

## Decision
`EtcdQuorumBackend` (and its fallback, `PostgresOutboxBackend`) protects
exactly one decision: whether a given execute action is approved to fire.
`RecoveryCase` state and the `AuditEntry` history live in ordinary Postgres,
on a normal Docker volume, single-node, outside the quorum-protected scope —
by design, not as an oversight.

## Why
Replicating the entire case-state store would be more impressive to say but
is not needed for the actual guarantee this project is claiming: that no
`(case_id, attempt_number)` pair ever executes twice, even across a leader
failover (Invariant 4). That guarantee only requires quorum on the
commit-approval key, not on every field of every case. Scoping it this way
keeps the system simpler to reason about and keeps the pitch's claims exactly
as strong as what's actually built.

## Consequences
- The demo's leader-kill beat (§12 step 6) must be narrated with the honest
  line from the builder doc: *"this protects the execute decision
  specifically — case history lives in a normal database."* Never "our whole
  system is Raft-replicated."
- Postgres durability for case state is a single Docker volume. This is
  judged sufficient (§15) because it's deliberately outside the
  quorum-protected scope, not because single-node Postgres is itself
  fault-tolerant.
- Any future feature that reads or writes `RecoveryCase`/`AuditEntry` must
  not assume etcd availability protects that data — only `CommitBackend.commit()`
  calls get that guarantee.
