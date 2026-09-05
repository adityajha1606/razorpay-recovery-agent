---------------------------- MODULE npcirules ----------------------------
EXTENDS Integers, Sequences

CONSTANTS
    MaxRetries,          \* Maximum retry attempts allowed (e.g., 3)
    NoticeLeadTime,      \* Minimum time between notice and execution (hours)
    Spacing              \* Sequence of minimum gaps between retries

VARIABLES
    retries_used,        \* Number of retries already executed
    last_executed_time,  \* Timestamp of last executed retry (0 if none)
    notice_sent_time     \* Timestamp of last sent notice

Init ==
    /\ retries_used = 0
    /\ last_executed_time = 0
    /\ notice_sent_time = 0

CanSendNotice ==
    notice_sent_time = 0

CanExecuteRetry ==
    retries_used < MaxRetries
    /\ (last_executed_time = 0 \/ now - last_executed_time >= Spacing[retries_used])
    /\ (notice_sent_time # 0 => now - notice_sent_time >= NoticeLeadTime)

IncrementRetry ==
    /\ retries_used' = retries_used + 1
    /\ last_executed_time' = now
    /\ notice_sent_time' = 0

Next ==
    \/ CanSendNotice /\ notice_sent_time' = now /\ UNCHANGED <<retries_used, last_executed_time>>
    \/ CanExecuteRetry /\ IncrementRetry

Invariant ==
    retries_used <= MaxRetries
    /\ (last_executed_time # 0 => now - last_executed_time >= 0)
    \* Add more invariants as needed

=============================================================================