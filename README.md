# Razorpay Hackathon · Track 3 — Recovery Agent

An agent that recovers failed UPI Autopay payments: classifies each failure
as retry-eligible or not, schedules retries at the moment they're
statistically likeliest to succeed, and executes them through a
quorum-committed, fully auditable state machine — while protecting both the
customer (from tripping their bank's fraud detection) and the shared UPI
rails (from being hammered during a failure spike). Every action is
traceable to the exact regulatory rule that justified it, and every recovery
claim is measured against an observed control baseline, not asserted.

**Full spec:** [`docs/BUILDER_DOC.md`](docs/BUILDER_DOC.md) — read this first, it supersedes everything else, including this README if the two ever disagree.
**Working in this repo (Claude Code, commands, guardrails):** [`CLAUDE.md`](CLAUDE.md)

## Status

Phase 0 scaffold only. What exists right now:

- The three foundational abstractions from §10 — `Clock` (§10.2),
  `CommitBackend` (§10.1), and config profile loading (§10.3) — implemented
  and unit-tested.
- The §6 data models, plus `assign_bucket()` enforcing Invariant 7 (bucket
  immutability).
- A FastAPI stub declaring the full §7 endpoint contract; every route
  currently returns HTTP 501 pointing at the section that specifies it.
- The 8 §8 invariants registered as named, `skip`ped test stubs so the
  checklist is visible before Phase 3 implements them for real.
- `docker-compose.yml` for Postgres + a 3-node etcd cluster.
- The hour-0 etcd spike (`spikes/etcd_spike.py`) — run this **before**
  anything else, per the how-to-build guide.

Not yet built: webhook listener, decline router, state machine, retry
optimizer, throttles, notice service, receipt renderer, dashboard. That's
Phases 1–3 — see §11 of the builder doc.

## Quickstart

```bash
# 1. Install
pip install -e ".[dev]"

# 2. Prove etcd works in your environment BEFORE building on it (see spikes/etcd_spike.py)
docker run -d --name etcd-spike -p 2379:2379 quay.io/coreos/etcd:v3.5.9 \
    etcd --advertise-client-urls http://0.0.0.0:2379 --listen-client-urls http://0.0.0.0:2379
python spikes/etcd_spike.py

# 3. Bring up the real infra
cp .env.example .env
docker compose up postgres      # Phase 1 default backend
docker compose up etcd1 etcd2 etcd3   # Phase 2 onward, once the spike above has passed

# 4. Run the API
uvicorn app.main:app --reload

# 5. Run the tests
pytest -q
```

## Commands

| Command | Purpose |
|---|---|
| `uvicorn app.main:app --reload` | Run the API |
| `pytest -q` | Run all tests |
| `pytest -q -m invariants` | Run just the 8 builder-doc §8 invariants |
| `docker compose up postgres` | Start Postgres (case state + outbox fallback) |
| `docker compose up etcd1 etcd2 etcd3` | Start the 3-node etcd cluster |

## Repo layout

```
app/
  core/
    clock.py            # §10.2 — RealClock / AcceleratedClock
    config.py           # §10.3 — loads config/npci_rules.yaml
    commit_backend.py   # §10.1 — EtcdQuorumBackend / PostgresOutboxBackend
  models.py              # §6 — the shared data-model contract
  main.py                 # FastAPI app, §7 endpoint stubs
config/
  npci_rules.yaml        # §10.3 — the only place retry/spacing/notice rules live
docs/
  BUILDER_DOC.md          # the spec — source of truth
  ROADMAP.md              # Phase 6 + anything else cut, per the scope-freeze rule
  WHAT_BROKE.md           # §13 template, filled in as things actually break
  adr/                    # architecture decision records
spikes/
  etcd_spike.py           # throwaway hour-0 de-risking script — not part of the app
tests/
  test_config_and_clock.py
  test_models.py
  test_commit_backend.py
  test_invariants.py      # the 8 §8 invariants, skipped until Phase 3
docker-compose.yml        # Postgres + 3-node etcd
```
