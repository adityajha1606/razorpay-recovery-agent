"""Advisory multi-armed bandit for retry slot selection.

Suggests the best retry timing slot based on observed success rates.
Epsilon-greedy balances exploration and exploitation. The state machine
still enforces NPCI legal floors; this is purely advisory.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class SlotStats:
    attempts: int = 0
    successes: int = 0


class RetrySlotBandit:
    """Epsilon-greedy bandit over named slots (e.g., 'immediate', '24h', '72h')."""

    def __init__(self, slots: list[str], epsilon: float = 0.1):
        self.slots = slots
        self.epsilon = epsilon
        self.stats: dict[str, SlotStats] = {s: SlotStats() for s in slots}

    def suggest(self) -> str:
        """Return a slot name using epsilon-greedy."""
        if random.random() < self.epsilon:
            return random.choice(self.slots)
        best_slot = self.slots[0]
        best_rate = -1.0
        for slot in self.slots:
            s = self.stats[slot]
            if s.attempts == 0:
                continue
            rate = s.successes / s.attempts
            if rate > best_rate:
                best_rate = rate
                best_slot = slot
        return best_slot

    def suggest_from(self, candidates: list[datetime]) -> datetime:
        """Suggest one candidate datetime from a legal list using epsilon-greedy."""
        if not candidates:
            raise ValueError("candidates must not be empty")
        if random.random() < self.epsilon:
            return random.choice(candidates)

        best_candidate = candidates[0]
        best_rate = -1.0
        for c in candidates:
            slot = self._candidate_to_slot(c)
            s = self.stats.get(slot, SlotStats())
            rate = (s.successes / s.attempts) if s.attempts > 0 else 0.5
            if rate > best_rate:
                best_rate = rate
                best_candidate = c
        return best_candidate

    def record_outcome(self, slot: str, success: bool) -> None:
        """Update stats for a slot."""
        if slot not in self.stats:
            return
        self.stats[slot].attempts += 1
        if success:
            self.stats[slot].successes += 1

    def estimated_success_rate(self, slot: str) -> float:
        """Return empirical success rate for a slot, or 0.5 if unseen."""
        s = self.stats.get(slot)
        if not s or s.attempts == 0:
            return 0.5
        return s.successes / s.attempts

    def _candidate_to_slot(self, candidate: datetime) -> str:
        """Map a candidate datetime to one of our named slots for stats."""
        hour = candidate.hour
        if hour < 12:
            return "immediate"
        elif hour < 24:
            return "24h"
        else:
            return "72h"