# 0002. One `Clock` abstraction for demo speed

Status: Accepted
Date: hour 0
Builder doc reference: §2 (two-clock rule), §10.2, §14 Q12

## Decision
A single `Clock` protocol (`now()`, `resolve_delay()`) with two
implementations — `RealClock` (passes durations through unchanged) and
`AcceleratedClock` (scales them by a configured `time_scale`). The live demo
runs on `AcceleratedClock` under the `demo` profile; the §8 property tests
always run against `RealClock` under the `prod` profile. There is no third,
demo-only code path anywhere in the business logic.

## Why
The alternative — a separate "fast" implementation of the scheduling logic
for the demo — would mean the thing being demoed and the thing being claimed
compliant are two different pieces of code. The builder doc's two-clock rule
(§2) exists specifically to rule that out: a judge asking "is that actually
72 hours?" needs to get "yes, here's the config" as a true statement, not a
plausible-sounding one.

## Consequences
- Every piece of scheduling logic must take delays as `timedelta` and run
  them through `Clock.resolve_delay()` — never call `time.sleep()` or do
  real-time arithmetic directly, or this guarantee silently breaks.
- Switching between `prod` and `demo` is a config profile change
  (`config/npci_rules.yaml`, §10.3), never a code change.
- This buys us nothing on its own if the property tests are ever run with
  `profile_name="demo"` by mistake — CLAUDE.md rule 3 exists to catch that
  in code review, not just in this ADR.
