"""Read-only case explainer — imports only audit/verify, never state_machine/commit_backend."""

from __future__ import annotations

from app.core.case_store import read_audit_trail
from verify_chain import verify_chain


def explain_case(case_id: str) -> str:
    entries = read_audit_trail(case_id)
    if not verify_chain(entries):
        return "Cannot explain: audit trail failed integrity verification."
    # Deterministic template (could be replaced by LLM call, but read-only)
    lines = [f"Case {case_id} audit trail:"]
    for e in entries:
        lines.append(f"{e.timestamp.isoformat()}: {e.from_state} → {e.to_state} ({e.rule_fired})")
    return "\n".join(lines)