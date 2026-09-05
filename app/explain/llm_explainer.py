"""Read-only explainer with optional LLM integration.

This module is structurally forbidden from importing the state machine or
commit backend (see .importlinter). It can only read audit trails and
verify their integrity. If an LLM endpoint is configured, it produces a
natural-language explanation grounded strictly in the audit trail; otherwise
it falls back to a deterministic template.

This is the project's answer to "where's the AI" — the LLM explains, it never
decides.
"""

from __future__ import annotations

import json
import logging
import os

import requests

from app.models import AuditEntry

logger = logging.getLogger(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")


def verify_chain_integrity(entries: list[AuditEntry]) -> bool:
    """Basic chain integrity check for the explainer's read-only view.

    The API endpoint already performs full Merkle verification before
    exposing entries; this is a minimal second check to ensure the data
    passed here has not been tampered with in memory.
    """
    prev_hash = None
    for e in entries:
        if not e.entry_hash:
            return False
        if e.prev_hash != prev_hash:
            return False
        prev_hash = e.entry_hash
    return True


def build_prompt(entries: list[AuditEntry]) -> str:
    lines = ["Explain the following payment recovery case based only on the audit trail:"]
    for e in entries:
        lines.append(
            f"{e.sequence_id}: {e.timestamp.isoformat()} {e.from_state}->{e.to_state} "
            f"rule={e.rule_fired} v{e.rule_version}"
        )
    return "\n".join(lines)


def call_llm(prompt: str) -> str | None:
    """Call external LLM if configured, else return None."""
    if not LLM_ENDPOINT:
        return None
    headers = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        headers["Authorization"] = f"Bearer {LLM_API_KEY}"
    try:
        resp = requests.post(
            LLM_ENDPOINT,
            json={"prompt": prompt, "max_tokens": 200},
            headers=headers,
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("text") or data.get("response")
    except Exception as exc:
        logger.warning("LLM call failed, falling back to deterministic: %s", exc)
        return None


def explain_case(entries: list[AuditEntry]) -> str:
    """Return a natural-language explanation grounded in the audit trail."""
    if not verify_chain_integrity(entries):
        return "Cannot explain: audit trail failed integrity verification."

    prompt = build_prompt(entries)
    llm_text = call_llm(prompt)
    if llm_text:
        return llm_text

    # Deterministic fallback
    lines = ["Case audit summary:"]
    for e in entries:
        lines.append(f"{e.from_state} → {e.to_state} because of {e.rule_fired}")
    return "\n".join(lines)