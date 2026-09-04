# Architecture Decision Records

One file per decision, numbered in the order they're made. Write it when you
make the call, not after — the builder doc's honesty rule (§2) applies to the
repo's history, not just the pitch.

Keep each ADR short: what was decided, what it costs, what it protects against.
If a later decision reverses an earlier one (e.g. the hour-20 etcd → Postgres
gate), add a new ADR that supersedes the old one — don't rewrite history.

## Template

```markdown
# NNNN. Title

Status: Proposed | Accepted | Superseded by NNNN
Date: YYYY-MM-DD
Builder doc reference: §X.Y

## Decision
One or two sentences. What did we choose.

## Why
The actual reasoning — including the alternative(s) considered and why they
lost, if that's not obvious from the builder doc alone.

## Consequences
What this costs us, what it protects, what it does NOT cover.
```

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-etcd-for-quorum-layer.md) | etcd for the quorum layer | Accepted |
| [0002](0002-clock-abstraction-for-demo-speed.md) | One `Clock` abstraction for demo speed | Accepted |
| [0003](0003-quorum-scope-execute-approval-only.md) | Quorum scope is execute-approval only | Accepted |
