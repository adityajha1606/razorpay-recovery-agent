"""Advisory classification proposer for unrecognized decline codes.

Read-only with respect to the state machine; it only suggests a class and
reasoning for manual review. Never auto-executes.
"""

from __future__ import annotations

import json
import logging
import os

import requests

logger = logging.getLogger(__name__)

LLM_ENDPOINT = os.getenv("LLM_ENDPOINT", "")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")


def propose_classification(reason_code: str, error_reason: str | None) -> dict:
    """Return a proposed decline_class and explanation.

    If an LLM endpoint is configured, it generates the proposal. Otherwise,
    falls back to a simple heuristic (default business).
    """
    prompt = (
        f"Classify this payment failure: reason_code={reason_code}, "
        f"error_reason={error_reason}. Is it technical (retryable) or business "
        f"(non-retryable)? Respond with JSON {{\"class\": \"technical\" or \"business\", "
        f"\"confidence\": 0.0-1.0, \"reasoning\": \"...\"}}"
    )

    if LLM_ENDPOINT:
        headers = {"Content-Type": "application/json"}
        if LLM_API_KEY:
            headers["Authorization"] = f"Bearer {LLM_API_KEY}"
        try:
            resp = requests.post(
                LLM_ENDPOINT,
                json={"prompt": prompt, "max_tokens": 100},
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            # Expecting response text containing JSON; parse if possible
            try:
                return json.loads(data.get("text") or data.get("response"))
            except Exception:
                pass
        except Exception as exc:
            logger.warning("LLM proposal failed, falling back to heuristic: %s", exc)

    # Deterministic fallback
    return {
        "class": "business",
        "confidence": 0.1,
        "reasoning": "Unknown code defaulting to business/non-retryable per NPCI safety policy.",
    }