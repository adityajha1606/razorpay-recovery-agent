"""Advisory survival analysis for time-to-recovery estimation.

Uses a simple Kaplan-Meier estimator over synthetic historical data to
estimate the probability that a payment recovers naturally by a given time.
This is purely advisory; the state machine never relies on it.
"""

from __future__ import annotations

from datetime import timedelta

# Synthetic recovery times (hours) for different reason codes.
# In production, this would be learned from real Razorpay data.
_SYNTHETIC_RECOVERY_TIMES_HOURS: dict[str, list[float]] = {
    "bank_server_down": [2, 5, 8, 12, 24, 30, 48, 72, 100],
    "network_timeout": [1, 3, 6, 10, 24, 36, 50, 72],
    "upi_timeout": [2, 4, 7, 12, 24, 40, 60, 80],
    "insufficient_funds": [24, 48, 72, 120, 168, 240, 336],  # often waits for salary
    "issuer_unavailable": [1, 2, 4, 8, 24, 48, 72],
    "generic": [5, 10, 20, 30, 50, 80, 120],
}


def estimate_recovery_probability(
    reason_code: str,
    elapsed_hours: float,
) -> float:
    """Return estimated probability of natural recovery after elapsed_hours."""
    times = _SYNTHETIC_RECOVERY_TIMES_HOURS.get(
        reason_code, _SYNTHETIC_RECOVERY_TIMES_HOURS["generic"]
    )
    recovered = sum(1 for t in times if t <= elapsed_hours)
    # Kaplan-Meier style estimate: proportion not yet recovered
    # We return the cumulative probability of recovery = recovered / total
    return recovered / len(times)


def survival_curve(reason_code: str, max_hours: float = 72) -> list[dict]:
    """Return a list of {elapsed_hours, recovery_probability} points."""
    points = []
    step = max_hours / 20  # 20 points
    elapsed = 0.0
    while elapsed <= max_hours:
        prob = estimate_recovery_probability(reason_code, elapsed)
        points.append({"elapsed_hours": round(elapsed, 1), "recovery_probability": prob})
        elapsed += step
    return points