# Demo Script: Escrow

Total run time: about 4.5 minutes if nobody interrupts you, which they will. Practice it twice before you show it to a judge. The times below are targets, not a stopwatch you need to hit exactly.

Expected outputs below are illustrative: they show the shape of what each endpoint returns, not a guaranteed byte-for-byte match. Check them against your actual running service once before you present, and adjust field names if they've drifted.

## Before you start

- `docker-compose up -d etcd1 etcd2 etcd3 postgres` is running and healthy.
- `CONFIG_PROFILE=demo`, `USE_ETCD=true`, `USE_POSTGRES=true`, and `RAZORPAY_WEBHOOK_SECRET` are exported in every terminal you'll type commands in.
- `pytest -q` has been run once tonight, so if a judge asks "does it actually pass," you already know the number.
- Three terminal tabs ready for beats 6 and 7: one instance on port 8001 already running, two more ready to start.
- Browser tabs open and already loaded once, so nothing has to cold-start on stage: `/dashboard`, `/cluster`.
- `docs/WHAT_BROKE.md` open in a tab, for beat 8.

## Timing at a glance

| Beat | What happens | Target time | Running total |
|---|---|---|---|
| 0 | Opening | 0:15 | 0:15 |
| 1 | Failed payment arrives | 0:20 | 0:35 |
| 2 | Agent refuses the easy thing | 0:25 | 1:00 |
| 3 | Fast-forward the clock | 0:30 | 1:30 |
| 4 | Execute and recover | 0:25 | 1:55 |
| 5 | Independent verification | 0:25 | 2:20 |
| 6 | Multi-worker exactly-once | 0:35 | 2:55 |
| 7 | Chaos: kill the leader | 0:25 | 3:20 |
| 8 | The verifier bug, told straight | 0:25 | 3:45 |
| 9 | Honest limitations | 0:20 | 4:05 |
| 10 | Close | 0:15 | 4:20 |

## Beat 0: Opening (0:00 to 0:15)

Say: "Everyone builds agents that act. This one decides when it's safe to act."

Say: "It's called Escrow. It holds a retry until the conditions for releasing it are actually met, and it refuses to release it otherwise. That's the whole idea. Let me show you."

Pause half a second here. Let the line land before you touch the keyboard.

## Beat 1: A failed payment arrives (0:15 to 0:35)

Do:
```bash
curl -X POST localhost:8001/admin/simulate-failure \
  -H "Content-Type: application/json" \
  -d '{"instrument_id": "mandate_demo_01", "amount": 499, "failure_reason": "insufficient_funds"}'
```

Expect: a `case_id` back, state `NOTICE_PENDING`.

Show: the new case appear on `/dashboard`.

Say: "That's a failed Autopay mandate. The obvious move is to just retry it. That's also the wrong move, and here's why."

## Beat 2: The agent refuses the easy thing (0:35 to 1:00)

Say: "NPCI requires a notice period before you're allowed to retry a debit. The agent knows that before it knows anything else about this case."

Do:
```bash
curl -X POST localhost:8001/compliance/check -d '{"case_id": "<case_id>"}'
```

Expect:
```json
{
  "allowed": false,
  "reason": "notice_lead_time_not_elapsed",
  "rule_cited": "notice_lead_time=24h"
}
```

Say: "Allowed: false. Not because anything broke. Because the rule says not yet."

## Beat 3: Fast-forward the clock (1:00 to 1:30)

Say: "I'm not making you sit here for 24 hours, so there's a demo clock."

Do:
```bash
curl -X POST localhost:8001/admin/advance-clock -d '{"hours": 24}'
curl -X POST localhost:8001/admin/send-notice/<case_id>
```

Expect: state moves to `NOTICE_SENT`, then to `RETRY_SCHEDULED`, with a reasoning string from the bandit along the lines of: `"picked slot 2026-09-06T14:00, inside the legal window, outside peak hours"`.

Say: "The bandit picked that slot. It's advisory, it's suggesting a good time, not approving the retry. The approval happens somewhere else. Next beat."

## Beat 4: Execute and recover (1:30 to 1:55)

Do:
```bash
curl -X POST localhost:8001/admin/execute-retry/<case_id>
curl -X POST localhost:8001/admin/simulate-success -d '{"case_id": "<case_id>"}'
```

Expect: state `RETRY_EXECUTED`.

Do:
```bash
curl localhost:8001/cases/<case_id>/receipt
```

Show: the full transition history, each step with a rule citation attached.

Say: "Every transition on this list cites the exact NPCI or RBI clause that allowed it. Nothing here happened because a model decided it was probably fine."

## Beat 5: Prove it independently (1:55 to 2:20)

Do:
```bash
curl localhost:8001/cases/<case_id>/verify
curl localhost:8001/cases/<case_id>/verify_chain
```

Expect: both checks pass.

Say: "This isn't the system checking its own homework. The verifier is a separate implementation, reading the same audit trail and re-deriving the answer independently. If the state machine had a bug and the verifier shared it, this check would be worthless. It doesn't share the code, so it isn't."

## Beat 6: Multi-worker exactly-once (2:20 to 2:55)

Say: "Here's the part that's actually hard. Three separate processes, one case, twenty simultaneous requests to execute it."

Do (three terminals, or already running from before you started):
```bash
uvicorn app.main:app --port 8001 &
uvicorn app.main:app --port 8002 &
uvicorn app.main:app --port 8003 &
python scripts/concurrency_test_multi.py <case_id> 20
```

Expect: one commit, nineteen rejections.

Say: "That's the etcd quorum enforcing it, not application code. No single process gets to decide alone."

If it comes back with anything other than exactly one commit: stay calm, say "let me rerun that," and run it again. It's a live distributed system, not a slide, so one retry is normal. Two odd results in a row means check that all three instances actually joined the same etcd cluster before you continue.

## Beat 7: Chaos, kill the leader (2:55 to 3:20)

Do:
```bash
curl -X POST localhost:8001/admin/chaos/kill-leader
```

Show: `/cluster` or `GET /cluster/status`, watch the leader change.

Say: "etcd specifically protects the execute-approval decision. Case history lives in Postgres, so losing a leader doesn't lose data, it just means a new node takes over deciding what's allowed to commit next."

If leader election takes a couple of seconds: keep talking through it. Dead air on a chaos demo reads as broken. Narrating the wait reads as understanding the system.

## Beat 8: The verifier bug, told straight (3:20 to 3:45)

Say: "For the first 20 hours of building this, the verifier had a real bug. It compared the wrong pair of timestamps, so its notice-lead-time check always passed, whether or not the rule had actually been respected. I caught it once I added the demo clock and could fast-forward time in a test. It's written up honestly in WHAT_BROKE.md, because I'd rather a judge hear it from me than find it themselves."

## Beat 9: Honest limitations (3:45 to 4:05)

Say, plainly, no hedging: "There's no real Razorpay webhook capture yet. It's validated against synthetic payloads. The 72-hour spacing and 48-hour window are my own defaults, not NPCI's. Admin endpoints are unauthenticated in this demo profile. All the ML in this system, the bandit, the survival model, the distress detector, is advisory. None of it gets a vote on execution. There's a read-only LLM explainer that answers questions about a case after the fact, grounded in the audit trail. It doesn't decide anything either."

## Beat 10: Close (4:05 to 4:20)

Show: dashboard metrics, recovery rate against the control group.

Say: "Built on real NPCI rules, verified by property tests, authorized by consensus. That's Escrow."

Stop talking. Let the last line sit for a second before taking questions.

## If you only have 90 seconds

Cut it to: Beat 0 (opening), a compressed pass through Beats 2 to 4 (refuse, fast-forward, execute, glance at the receipt), Beat 6 (multi-worker exactly-once, it's the single most convincing thing in the whole demo), and Beat 10 (close). Drop the chaos beat and the verifier-bug story entirely if you're this tight. Keep them ready for a follow-up question instead, they're worth having, just not worth spending your only 90 seconds on.