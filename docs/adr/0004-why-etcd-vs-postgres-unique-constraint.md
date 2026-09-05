# 0004. Why etcd Raft vs. a Postgres Unique Constraint

Status: Accepted
Date: 2026-09-05
Builder doc reference: §10.1, ADR 0001, ADR 0003

## Context

A retry can only execute once. If two workers pick up the same case at the same time, whether from a redeploy overlap, a retried webhook, or a bug in the scheduler, only one of those attempts is allowed to actually commit `RETRY_EXECUTED`. The state machine doesn't solve this by itself: it decides what should happen to a case next, not who gets to act on that decision when two processes ask at the same moment.

For the hackathon, Escrow runs as three independent uvicorn processes specifically to prove that a single case can't be double-executed under real contention, not simulated contention. That's a distributed coordination problem, and it needed an actual mechanism behind it, not a comment in the code assuming a single instance.

Two backends exist to solve this: an etcd-backed commit path (`app/core/commit_backend.py`) and a Postgres-backed outbox with a unique constraint on `(case_id, attempt_number)`. Both are implemented and selectable via `USE_ETCD` / `USE_POSTGRES`. This ADR is about which one is the real answer and which one is sitting on the shelf for later, since shipping two backends without saying which one to trust just moves the ambiguity into the config file.

## Decision

Use etcd (Raft) for commit approval whenever multiple independent workers need to agree without shared memory. That's the path the demo exercises: three uvicorn processes, one etcd cluster, and a kill-leader chaos test to prove the quorum survives losing a node.

For a single-instance deployment, `PostgresOutboxBackend` with a unique constraint on `(case_id, attempt_number)` is sufficient, and it's the backend I'd recommend by default outside of a multi-instance demo.

## Why this split, and not just one backend

Exactly-once execution for a single writer is a solved problem. A unique constraint plus a transaction gets you there, and it's already what most production systems reach for first. There was no reason to bring in a consensus protocol to solve a single-writer problem, and claiming I needed one would misrepresent what etcd is actually for.

Multiple independent writers are a different problem. A unique constraint on one Postgres instance only guarantees exactly-once if every writer is talking to that same instance. Once workers might not agree on which instance is authoritative, or the case store ends up sharded down the line, something has to hold a leader election and get every worker to agree on one outcome even when they can't all see the same database at the same moment. etcd is the well-understood way to get that without writing a consensus algorithm from scratch, which is not something I trust myself to get right under a hackathon deadline.

Small aside: I built the Postgres path first because I assumed it would be the whole answer. I added etcd once it became clear that "run three processes and prove they don't double-execute" made a far more convincing demo than a unique constraint ever could, and a unique constraint alone doesn't give you a kill-the-leader moment to point a judge at.

## Consequences

- The commit backend is config-driven via `USE_ETCD` / `USE_POSTGRES`, so switching backends never means touching the state machine or the verifier.
- Both backends implement the same idempotency contract and share the same contract tests, so a case committed through etcd and a case committed through Postgres look identical to the state machine.
- The case store is a derived projection of the commit log, not the source of truth on its own. That means reconciliation can rebuild case state from the log if the two ever drift, which starts to matter the moment there are two commit backends that could theoretically disagree.
- Running etcd is real operational weight a single-instance deployment doesn't need. Anyone deploying this for actual use, with one worker, should default to Postgres and skip standing up a 3-node cluster for a problem they don't have.

## Alternatives Considered

- **Do nothing, rely on idempotent retries at the caller.** Works only if every future caller correctly deduplicates on `case_id`, which pushes the exactly-once guarantee out to every consumer instead of enforcing it once, centrally. Rejected: too easy to violate by accident, and the whole point of this system is not depending on every caller getting it right.
- **A distributed lock in Redis.** Faster to stand up than etcd, but a lock that expires mid-execution because of a slow network call is precisely the failure mode this system exists to prevent. Rejected: locks can be silently lost; Raft consensus can't silently disagree with itself.
- **Postgres `SELECT ... FOR UPDATE` across instances.** Only works if every instance points at the same Postgres, which is the same assumption the unique-constraint approach already makes. It doesn't solve the no-shared-database case any better than the constraint does, so it didn't earn a separate backend.
- **Zookeeper instead of etcd.** Solves the same problem. Passed on it mainly because etcd's HTTP and gRPC API and single static binary were simpler to run inside a hackathon demo environment, not because of any real technical objection to Zookeeper.