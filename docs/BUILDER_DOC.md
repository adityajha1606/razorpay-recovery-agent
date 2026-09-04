### Razorpay Hackathon · Track 3: AI Revenue Recovery
*(v3 — meticulous pass; see §14 items 12–17 for what changed this round and why)*

---

## 1. Project Overview

An agent that recovers failed UPI Autopay payments by classifying each failure as retry-eligible or not, scheduling retries at the moment they're statistically likeliest to succeed, and executing them through a quorum-committed, fully auditable state machine — while actively protecting both the customer (from tripping their own bank's fraud detection) and the shared UPI rails (from being hammered during a failure spike). Every action is traceable to the exact regulatory rule that justified it, and every recovery claim is measured against an observed control baseline, not asserted. The core claim: this isn't a retry loop, it's a compliant, measurable, self-protecting recovery system — and every part of that claim is something a judge can check themselves, not just something you say.

---

## 2. Vision & Decision Philosophy

**North star:** every feature in this project exists to make one claim provable on stage — *this agent recovers real money, inside real regulatory limits, and a judge can verify every word of that from the log themselves.* If a feature doesn't strengthen that claim, it doesn't belong in the next 48 hours, however impressive it sounds in isolation.

**Priority order when two things compete for the same hour** — use this to end arguments quickly, not start them:
1. Correctness of the headline recovery number and the 8 invariants
2. Regulatory compliance behavior (retry cap, spacing, notice)
3. The three protected signature features (below)
4. The remaining three signature features
5. Demo polish and rehearsal
6. Everything else

**Bug-triage rule:** if a bug threatens the headline number or an invariant, fix it immediately, whatever phase you're in. If it's cosmetic — a label, a color, an edge case nobody will click on stage — write it down and keep moving. The fastest way to lose is spending hour 34 polishing something that was never going to be graded.

**The two-clock rule:** the code running in the live demo and the code you're claiming is compliant must be the *same* code, at two different speeds — never two different implementations. One `Clock` abstraction (§10.2), not a hand-waved "in real life this takes 3 days." A judge who asks "is that actually 72 hours?" gets "yes, here's the config," not a shrug.

**The honesty rule:** claim exactly what you built, prove exactly what you claim, no more. The quorum layer protects one specific decision, not your whole system (§10.3) — say that specifically. If etcd falls back to Postgres at hour 20, say so in the "what broke" section, not around it. The entire pitch is that this can be trusted to run unattended *because* it's precise about what's actually guaranteed — overclaiming anywhere breaks the exact trust you're building everywhere else.

**Scope freeze:** by hour 30, no new features start — only the six signature features and their supporting infrastructure get finished. After hour 30: bug fixes, demo rehearsal, repo hygiene, nothing else. A great idea at hour 35 goes on the roadmap slide (Phase 6), not into the codebase.

**Six features carry this project. Build these deep, not just present them:**
1. Soft/hard decline router
2. Retry-budget optimizer with time-based release
3. Fraud-trigger self-throttle (per-instrument)
4. System-wide self-throttle (backpressure)
5. Pre-debit notice deduplication ("do not disturb" sentinel)
6. Rule-citation receipt per case

**Supporting infrastructure (necessary, not the story):** webhook ingestion, state machine core, etcd quorum layer + fallback, the Clock abstraction, control/treatment split with observed outcomes, dashboard, property tests.

**If you can only protect three when things go wrong:** #1, #2, #3, in that order. #1 is foundational — nothing downstream means anything without it. #2 is the biggest "wow." #3 is the cheapest, highest-surprise line in the pitch.

---

## 3. Final Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Consensus / quorum | `etcd` via `etcd3` Python client, 3-node docker-compose | Protects the execute decision specifically — see §10.3 for exact scope |
| Fallback commit backend | Postgres + outbox table | Swappable via one interface — §10.1 |
| Time source | `Clock` abstraction, `RealClock` / `AcceleratedClock` | Same code, two speeds — §10.2 |
| State machine | Deterministic Python, single-threaded apply loop | No async surprises during a live demo |
| Webhook / API | FastAPI | |
| Classifier | Rule-based, config-driven mapping table | XGBoost is roadmap-only, not core |
| Dashboard | Streamlit (metrics, tables, receipts) + one standalone HTML/JS page (cluster status only) | Streamlit's rerun model doesn't animate a live cluster well |
| Testing | `pytest` + `hypothesis` | 8 invariants, see §8 — always checked against `prod` config |
| Deployment | Docker Compose | |
| External APIs | Razorpay test-mode webhooks | Verify payload shape in Phase 0, don't assume it |

---

## 4. Component Architecture

- **Webhook Listener** — receives Razorpay test-mode failure events. Handles a brand-new failure (opens a `RecoveryCase`) and a repeat event against an existing `case_id`/`mandate_id` (needed for control-outcome observation and per-attempt result recording).
- **Decline Router** — soft/hard classification (§9A), run on every attempt. Unmapped codes default to `business/non-retryable` + manual-review — never guess retryable.
- **Control/Treatment Splitter** — deterministic hash of `case_id`, applied once at first classification, only within the retry-eligible population. Bucket is immutable for the case's life (Invariant 7). Control cases stay observed, bounded by `control_observation_deadline` — past it with no signal, auto-close as `CONTROL_STILL_FAILED`, so the incremental-recovery baseline is always resolvable, never open-ended.
- **Retry Optimizer** — chooses slot and reasoning (§9B).
- **Throttle Layer** — instrument-level (§9C) and system-wide (§9D) checks before any execution. Cycling through `THROTTLED` never increments `attempt_number` or consumes retry budget — only an actual execution outcome does that (Invariant 8).
- **Commit Backend** — abstraction over etcd quorum or Postgres outbox (§10.1). Protects one decision — "does this execute action fire" — not the system's full state.
- **State Machine** — applies committed actions deterministically, appends to the audit log. Case state and history live in Postgres, a normal durable single-node store, *not* part of the quorum layer — a deliberate scoping choice, not an oversight (§10.3).
- **Notice Service** — batches and deduplicates pre-debit notices per instrument (§9E); fires fresh for every attempt.
- **Receipt Renderer** — read-time projection over the audit log (§9F).
- **Dashboard** — money metrics (agent-recovered headline, human-recovered on a separate line, control baseline alongside), escalation queue, compliance score, cluster status.

---

## 5. State Machine

```
RECEIVED
   → CLASSIFIED                                  [bucket assigned here — immutable, Invariant 7]
        → CONTROL_HELD                           [control bucket: logged, no action taken]
        → ESCALATED                              [treatment bucket, hard decline on attempt 1]
        → NOTICE_PENDING                         [treatment bucket, technical decline]

CONTROL_HELD → CONTROL_RECOVERED                  [later webhook shows natural success]
CONTROL_HELD → CONTROL_STILL_FAILED               [webhook signal seen, no recovery — OR deadline reached]

NOTICE_PENDING → NOTICE_SENT                      [batched per instrument, DND-aware]
NOTICE_SENT → RETRY_SCHEDULED                     [optimizer picks slot; ≥24h since this attempt's notice]
RETRY_SCHEDULED → THROTTLED → RETRY_SCHEDULED     [instrument or system throttle active — does NOT touch attempt_number]
RETRY_SCHEDULED → RETRY_EXECUTED                  [commit backend acknowledges]

RETRY_EXECUTED → RECOVERED                        [terminal, success — counts toward agent-recovered headline]
RETRY_EXECUTED → RETRY_EVAL                       [failed again — new PaymentFailureEvent, re-classify]

RETRY_EVAL → ESCALATED                            [retries_used == 3, or new decline_class == business]
RETRY_EVAL → NOTICE_PENDING                       [budget remains, still technical — next attempt needs its OWN fresh notice]

ESCALATED → RESOLVED_BY_HUMAN                     [terminal — resolution_note records outcome, see §14 item 14]
```

Two rules that aren't visible in the diagram but must hold in code:
- `attempt_number` and `retries_used` increment **only** on `RETRY_EXECUTED → RETRY_EVAL`. Any pass through `THROTTLED` is a no-op against the budget (Invariant 8).
- Invariants 2 and 6 (spacing) are evaluated against `executed_at`, never `scheduled_at` — a throttle-delayed execution is the thing that must respect the spacing floor, not the original plan for it.

---

## 6. Data Models

```python
RecoveryCase:                   # the stable anchor for one recovery effort
  case_id: str                  # = the first failed payment_id
  mandate_id: str
  instrument_id: str            # hashed VPA/account — never store raw
  original_amount: Decimal
  bucket: Literal["treatment", "control"]   # set once at first classification — IMMUTABLE (Invariant 7)
  retries_used: int
  state: str
  control_outcome: Literal["recovered_naturally", "still_failed", "unknown"] | None   # control-bucket only
  control_observation_deadline: datetime | None                                        # control-bucket only
  resolution_note: Literal["written_off", "recovered_manually"] | None                 # set on human resolution
  opened_at: datetime

PaymentFailureEvent:            # one per attempt — Razorpay likely issues a NEW payment_id per retry
  case_id: str
  payment_id: str               # Razorpay's id for THIS attempt specifically
  attempt_number: int
  reason_code: str
  decline_class: Literal["technical", "business", "unclassified"]
  amount: Decimal
  received_at: datetime

RetryDecision:
  case_id: str
  attempt_number: int
  scheduled_at: datetime
  executed_at: datetime | None
  reasoning: str
  commit_backend: Literal["etcd", "postgres_outbox"]
  commit_ref: str
  outcome: Literal["pending", "recovered", "failed"]

AuditEntry:
  sequence_id: int
  case_id: str
  from_state: str
  to_state: str
  rule_fired: str
  rule_version: int
  timestamp: datetime
  actor: Literal["agent", "human"]

NoticeRecord:
  instrument_id: str
  mandate_ids: list[str]
  sent_at: datetime
  dnd_deferred: bool
```

---

## 7. API Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /webhook/payment-failed` | Razorpay test-mode ingestion — new cases *and* repeat events against an existing case |
| `GET /dashboard/metrics` | at-risk, `agent_recovered` (headline), `human_recovered` (separate line), control baseline, retries used, compliance score |
| `GET /cases/{case_id}/receipt` | rule-citation trace (§9F) |
| `POST /admin/escalations/{case_id}/resolve` | human decision + `resolution_note`, appended to audit log |
| `POST /admin/chaos/kill-leader` | demo trigger |
| `POST /admin/chaos/spike` | demo trigger for system throttle (§9D) |
| `GET /cluster/status` | node/leader status, polled by the standalone HTML view |

`GET /dashboard/metrics` deliberately never sums `agent_recovered` and `human_recovered` into one number in the API response — keep them separate all the way to the wire, so no downstream view can accidentally conflate them.

---

## 8. Property Test Invariants (Hypothesis)

All 8 are checked against the **`prod`** config profile (§10.2) regardless of which `Clock` the running app is wired to — this is what lets the compliance claim stay true independent of demo speed.

1. No case ever executes with `retries_used > 3`.
2. No two retries on the same case fire closer together than the configured spacing, measured by `executed_at`.
3. No retry executes without either quorum acknowledgment (etcd) or a durable outbox write (fallback).
4. No `(case_id, attempt_number)` pair ever executes twice.
5. **Monetary reconciliation (exact):** for every case, `recovered_amount` is always exactly `0` or exactly `original_amount`, and `sum(recovered_amount)` across all `RECOVERED`, **treatment-bucket** cases equals `sum(original_amount)` of exactly that set. Control-bucket and human-resolved recoveries never enter this sum.
6. **Fraud-throttle spacing:** no two agent-initiated actions on the same `instrument_id`, across all its mandates, fire within the self-imposed minimum gap, measured by `executed_at`.
7. **Bucket immutability:** once set at first classification, `bucket` never changes for the life of a case.
8. **Throttle budget-neutrality:** any number of `RETRY_SCHEDULED → THROTTLED → RETRY_SCHEDULED` cycles never changes `attempt_number` or `retries_used`. Only a genuine execution outcome does.

---

## 9. Signature Feature Specs

**A. Soft/Hard Decline Router**
Config-driven map: `reason_code → {class, retryable}`. Run on every attempt, not just the first. Unmapped codes default to `business/non-retryable` + manual-review flag.

**B. Retry-Budget Optimizer (Time-Based Release)**
Earliest possible slot is bounded below by the notice lead time for *this* attempt — never "immediate." Later slots follow the NPCI spacing config. Each attempt requires its own fresh notice (`RETRY_EVAL → NOTICE_PENDING`). Exception: `insufficient_funds` biases toward the customer's likely salary window (day 1–7 of month) only when that window overlaps the interval between the earliest legal slot and the latest slot that still preserves the attempt; otherwise, fall back to the earliest legal slot. Every decision stores its reasoning string for the receipt.

**C. Fraud-Trigger Self-Throttle**
Per-`instrument_id`, a rolling minimum gap between agent-initiated actions, tighter than NPCI's own floor — protects against the bank's own fraud engine, not the regulation. Applies across all mandates on that instrument.

**D. System-Wide Self-Throttle**
Token bucket, global, applied to every execution through `CommitBackend` — first attempts and retries alike. Excess actions enter `THROTTLED` and drain on later ticks without touching retry budget (Invariant 8).

**E. Pre-Debit Notice Deduplication**
Group by `instrument_id`; one batched notice per day per instrument, not one per mandate. DND window respected unless honoring it would leave under 24h before the earliest affected debit — compliance floor wins over UX preference in that case, by design.

**F. Rule-Citation Receipt**
Read-time render over a case's `AuditEntry` history. Each line names the exact rule and config value that justified that transition.

---

## 10. Foundational Abstractions & Config

Build these three things first — hour 0–2 — because everything else assumes they exist.

### 10.1 CommitBackend

```python
class CommitBackend(Protocol):
    def commit(self, action: RetryDecision) -> bool: ...
```

`EtcdQuorumBackend` and `PostgresOutboxBackend` both implement it. **Hard gate at hour 20:** if etcd isn't clean and demo-reliable by then, flip one config flag to `PostgresOutboxBackend` and don't touch etcd again. Say so in the pitch: *"in production this is etcd-backed; today's build runs single-writer with the identical idempotency contract."*

### 10.2 Clock

```python
class Clock(Protocol):
    def now(self) -> datetime: ...
    def resolve_delay(self, real_delay: timedelta) -> timedelta: ...
```

`RealClock` passes real durations through unchanged. `AcceleratedClock` scales them by a configured factor for the live demo (e.g., 72h → 72s) — same decision logic, same code path, different speed. The property tests in §8 never use `AcceleratedClock`; they validate the `prod` profile's real constants directly.

### 10.3 Config Profiles

```yaml
npci_rules:
  max_retries: 3
  notice_lead_time: 24h
  spacing:
    - 0h      # bounded below by notice_lead_time in practice
    - 72h
    - 168h    # ~7 days

profiles:
  prod:
    time_scale: 1x
  demo:
    time_scale: 3600x   # 1 real hour becomes 1 second; tune during rehearsal
```

**Scope honesty note:** `EtcdQuorumBackend` protects exactly one decision — whether a given execute action is approved to fire. It does not replicate `RecoveryCase` state, audit history, or anything else; that lives in ordinary Postgres, durable via a normal Docker volume, single-node, not quorum-protected. This is a deliberate scope, not a gap — say it this way in the pitch, not "our whole system is Raft-replicated."

---

## 11. Phased Implementation Checklist

**Phase 0 (0–2h) — Prove the ground truth**
- Hit Razorpay test-mode webhook for real; confirm actual `reason_code` field/values, and whether test-mode allows simulating a second event against the same mandate.
- Stand up `CommitBackend` (§10.1) and `Clock` (§10.2) with both implementations stubbed.
- Write the NPCI config (§10.3) with `prod` and `demo` profiles from the start — not retrofitted later.

**Phase 1 (2–12h) — Single-writer core**
- Webhook listener (handles new *and* repeat events), decline router (§9A), core state machine through `RECOVERED`/`ESCALATED`.
- `PostgresOutboxBackend` as default; `RealClock` wired to `prod` profile.
- Control/treatment hash split with `control_observation_deadline` set at case creation.
- Dashboard v0: raw money numbers, `agent_recovered` only.
- **Gate:** one case traverses the full loop end to end; a control-bucket case resolves via a second fixture event (manually fired — the deadline-sweep job isn't needed yet).

**Phase 2 (12–20h, hard gate at 20) — Trustworthiness layer**
- Swap to `EtcdQuorumBackend`, 3-node cluster.
- Notice dedup/DND (§9E), fired per-attempt. Fraud-throttle spacing (§9C) enforced as Invariant 6.
- **Gate:** per §10.1 — flip the flag and move on if not clean.

**Phase 3 (20–30h) — Prove the number**
- Retry-budget optimizer (§9B), system throttle (§9D) with `THROTTLED` state that never touches retry budget (Invariant 8).
- `AcceleratedClock` wired for the `demo` profile; verify property tests still run against `prod`.
- Deadline-sweep job for `control_observation_deadline` (small periodic check, not user-facing).
- `resolution_note` on human escalation resolution; dashboard splits `agent_recovered` / `human_recovered`.
- Dashboard v1: treatment recovery vs. observed control baseline, retries by attempt, compliance score (free aggregate of the 8 invariants' pass rate).
- All 8 Hypothesis invariants passing against `prod` config.

**Phase 4 (30–40h) — Demo theater**
*(Scope freeze from here — see §2.)*
- Escalation review UI. Leader-kill button + cluster-status view; spike-throttle button.
- Fixture replay script including one control case that resolves naturally, so the baseline is real. Rehearse both chaos beats twice. Record backup video.

**Phase 5 (40–48h) — Pitch, repo, buffer**
- Record pitch (money story first, compliance as "why you can trust it unattended," scope honesty per §10.3 in the close).
- Clean repo/README/commit history. Fill "what broke" template (§13) honestly.
- Real buffer.

**Phase 6 — Roadmap slide only, not built**
- Cross-mandate distress signal, trained classifier, other Track 3 loss types (checkout abandonment, overdue receivables).
- Scale story: Kafka ingestion; shard consensus state per mandate/merchant; optimizer as periodic batch over learned success curves; audit trail to a data lake; deterministic idempotency-key hashes.
- Hash-chained verifiable log — cut. The etcd log is already stronger proof than most teams will have.

---

## 12. Demo Script Outline

1. Open on the business problem — money lost to failed payments (10–15s).
2. Fixture replay fires a failure → soft-decline classification → notice sent → retry scheduled, optimizer's reasoning shown on screen (running on the `demo` time-scale, stated as such).
3. Click that case's receipt — the rule-citation trace renders live.
4. Show incremental recovery: treatment rate vs. control's *observed* natural rate — the number that survives a skeptical question.
5. Hit the spike button — system throttle visibly staggers execution.
6. Kill the leader node live — recovery continues, new leader elected, nothing lost. One sentence of honest scope here: *"this protects the execute decision specifically — case history lives in a normal database."*
7. Close on the compliance score and total recovered figure, `agent_recovered` and `human_recovered` shown as separate lines, with one line on the NPCI-rule sourcing (§14, Q3).

---

## 13. "What Broke" Template

| What broke | When discovered | Root cause | Fix applied | What we'd do differently |
|---|---|---|---|---|
| *(example)* etcd quorum commit flaky under Docker networking | Hour ~18 | *(fill in)* | Flipped `CommitBackend` to Postgres outbox per §10.1 gate | *(fill in)* |
| | | | | |
| | | | | |

Leave rows blank until something actually breaks. Fill honestly — this section is graded on candor.

---

## 14. Resolved Open Questions

1. **etcd vs. raw Raft vs. CockroachDB:** etcd — legible leader/follower story, no extra operational surface. Budget real debug time for `etcd3` specifically; §10.1's fallback exists because of that risk, not despite it.
2. **Control group implementation:** deterministic hash of `case_id`, retry-eligible population only.
3. **NPCI rule precision:** cite as "publicly documented industry summary, not the primary circular" — proactively, before a judge asks.
4. **Failure-reason taxonomy from test-mode:** Phase 0, non-negotiable; synthetic-event injector as fallback.
5. **Fallback specifics:** §10.1.
6. **Dashboard tech:** Streamlit + one standalone HTML/JS page for the cluster visual.
7. **Invariant count:** 8 — see §8 for the full reasoning behind each addition.
8. **"What broke" flexibility:** §13's template is generic on purpose.
9. **Case vs. attempt identity:** `case_id` as stable anchor, `payment_id` scoped per attempt.
10. **Control group observability:** control cases stay observed via repeat webhook events, not terminal.
11. **Headline metric scope (agent vs. control):** `agent_recovered` counts treatment-bucket, agent-driven recoveries only.
12. **Demo speed vs. real compliance (found in this pass):** one `Clock` abstraction, two profiles, tests always run against `prod` — see §10.2 and the two-clock rule in §2.
13. **Control observation window (found in this pass):** bounded by `control_observation_deadline`; unresolved cases auto-close rather than waiting indefinitely.
14. **Headline metric scope, part two — human resolution (found in this pass):** manually recovered escalations are tracked via `resolution_note` and reported on a separate `human_recovered` line, never folded into `agent_recovered` — same logic as excluding control recoveries, applied at the escalation stage instead.
15. **Throttle vs. retry budget (found in this pass):** cycling through `THROTTLED` never consumes NPCI retry budget; only a genuine execution outcome does — now Invariant 8.
16. **Which timestamp spacing invariants use (found in this pass):** `executed_at`, not `scheduled_at` — a throttle-delayed execution is what actually has to respect the spacing floor.
17. **Exact scope of the quorum guarantee (found in this pass):** etcd protects the execute-approval decision specifically; case state lives in ordinary Postgres, outside quorum, by design — stated explicitly in §10.3 and in the demo script's close so the pitch never overclaims what's actually guaranteed.

---

## 15. Assumptions

- Team size ~3; adjust phase hour-budgets proportionally if solo or larger.
- Razorpay's rules on pre-built code have been checked separately — this doc assumes building starts at hackathon hour 0.
- "Instrument" = hashed VPA or account+IFSC, never stored raw.
- "Recovered" means the full original amount was collected — no partial-recovery handling in this build.
- Razorpay test-mode permits simulating a second webhook event against the same mandate — verify in Phase 0; if not possible, the synthetic-event injector (open question 4) must cover this too.
- Postgres durability via a single Docker volume is sufficient for case state, since it is deliberately outside the quorum-protected scope (§10.3) — it does not need to be distributed itself.
