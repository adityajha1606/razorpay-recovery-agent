# Roadmap

Phase 6 material from `docs/BUILDER_DOC.md` §11 — deliberately **not built** in the
48-hour window. Nothing here should exist in the codebase; if you're tempted to
sneak a piece of this in after hour 30, that's the scope-freeze rule (§2) telling
you to write it down here instead.

Anything else the team decides to cut mid-build also lands here, with a one-line
reason, so "we cut X" is a documented decision and not just a thing that quietly
never happened.

## Carried over from the builder doc

- **Cross-mandate distress signal** — detect a customer struggling across
  multiple mandates, not just one, and adjust routing/notice strategy accordingly.
- **Trained classifier** — replace the config-driven decline router (§9A) with
  a learned model once there's enough labeled attempt history to train on.
  XGBoost was the placeholder discussed; nothing here is committed to that choice.
- **Other Track 3 loss types** — checkout abandonment, overdue receivables —
  as separate recovery pipelines reusing this project's state machine shape.
- **Scale story:**
  - Kafka ingestion in place of direct webhook handling.
  - Shard consensus state per mandate/merchant instead of one global etcd keyspace.
  - Retry optimizer as a periodic batch job over learned success curves, rather
    than a per-attempt rule evaluation.
  - Audit trail exported to a data lake for long-term / cross-case analytics.
  - Deterministic idempotency-key hashes (as opposed to the current opaque
    `commit_ref`).
- **Hash-chained verifiable log** — explicitly cut, not deferred. The etcd
  quorum log is already stronger proof than most teams will have; a
  hash-chain on top of it wasn't judged worth the build time. Revisit only if
  the etcd story itself gets challenged in a way a hash-chain would actually answer.

## Cut during the build

_(Add a line per item as decisions get made — what it was, and why it didn't
make the cut. Empty is fine for hour 0.)_

| Item | Why it was cut | Decided at |
|---|---|---|
| | | |
