@docs/BUILDER_DOC.md

# Working in this repo

Everything about **what** to build is in `docs/BUILDER_DOC.md`, imported above — it is the single source of truth and it supersedes anything below if the two ever conflict. This file only covers **how** to work here: commands, guardrails, and process.

## Build & test

- Run API: `uvicorn app.main:app --reload`
- Run tests: `pytest -q`
- Start etcd cluster: `docker compose up etcd`
- Start Postgres: `docker compose up postgres`
- Run just the 8 invariants: `pytest -q -m invariants` (tag them with `@pytest.mark.invariants` as you write them)

## Non-negotiable rules

1. **Never write code that contradicts the builder doc.** If you find a conflict between what's asked and §-anything in the doc, stop and flag it — don't silently pick a side.
2. **The hour-20 and hour-30 gates are human calls, not yours.** At hour 20 (etcd vs. Postgres outbox, §10.1) and hour 30 (scope freeze, §2), you can surface facts — test output, error logs, what's actually working — and lay out the options, but a human types the decision. Don't keep pushing on etcd past the gate because it's "almost clean."
3. **All 8 Hypothesis invariants (§8) run against the `prod` config profile, never `demo`** — regardless of which `Clock` the app is wired to when you run them.
4. **No demo-only code paths.** `AcceleratedClock` and `RealClock` must drive identical logic, just different constants. If you find yourself writing an `if demo:` branch in business logic, that's a bug, not a feature.
5. **etcd protects exactly one decision** — execute-approval (§10.3). Never describe it, comment it, or build it as protecting case state, audit history, or "the whole system."
6. **No `# TODO` in committed code.** Deferred ideas go in `docs/ROADMAP.md`, not inline comments.
7. **Every public function gets a docstring and type hints.** Every error path is handled and logged with enough context to debug later — no bare `except: pass`.
8. **If a §8 test fails, fixing it outranks any new feature work** (the doc's bug-triage rule, §2).

## Quality bar

Build like this ships to production, not like it's a hackathon demo:

- Typed, documented Python; real package structure (`__init__.py`, clear module boundaries) — no dumping everything in one file.
- Defensive validation on every webhook/API payload entry point.
- The rule-citation receipt (§9F) is a design constraint, not a bolt-on feature: every state transition must be traceable to the exact rule/config value that caused it.
- Keep `docs/ROADMAP.md`, ADRs, and the README current as you build, not as a hour-47 scramble. A judge should be able to follow the architecture from the docs alone.
- The three protected features — decline router (#1), retry-budget optimizer (#2), fraud-trigger throttle (#3) — are never what gets cut. If something has to give, it's breadth elsewhere, logged to `docs/ROADMAP.md`.
- When you spot a design flaw, race condition, or missed invariant, say so immediately and propose a fix — then let the human decide. Don't quietly patch around it.

## Working style

- Use git worktrees for parallel work: `claude --worktree <name>` (or `-w`). Keep it to 3 active worktrees at a time — more gets hard for a 3-person team to supervise.
- When asking for an implementation, quote the exact section of `docs/BUILDER_DOC.md` rather than paraphrasing it from memory — e.g. "implement §10.1's `CommitBackend` protocol exactly as written." The doc is precise enough that this consistently beats a looser description.
- §6's data models are the shared contract across worktrees — point every session at them, not just the one building the state machine.
- Merge to `main` at each phase gate, not later. Small, frequent merges surface integration problems while they're still cheap.

## Docs in this repo

- `docs/BUILDER_DOC.md` — the spec (source of truth)
- `docs/ROADMAP.md` — Phase 6 ideas and anything cut for scope, not built
- `docs/adr/` — architecture decision records, one file per decision, written as you make them
- `docs/WHAT_BROKE.md` — §13's template, filled in honestly as things actually break
