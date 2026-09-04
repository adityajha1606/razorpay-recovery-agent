#!/usr/bin/env python3
"""
Independent audit-chain verifier.

Usage:
    python verify_chain.py <audit_export.json>

The JSON file should have the structure:
{
  "case_id": "pay_demo_003",
  "audit": [
      {
          "sequence_id": 1,
          "case_id": "pay_demo_003",
          "from_state": "RECEIVED",
          "to_state": "CLASSIFIED",
          "rule_fired": "create_case",
          "rule_version": 2,
          "timestamp": "2026-09-04T20:38:04.821288+00:00",
          "actor": "agent",
          "prev_hash": null,
          "entry_hash": "..."
      },
      ...
  ]
}

The script recomputes each entry's hash and verifies the chain.
"""

import hashlib
import json
import sys


def compute_hash(entry: dict) -> str:
    """Recompute the hash for a single audit entry dict."""
    serialized = json.dumps({
        "case_id": entry["case_id"],
        "from_state": entry["from_state"],
        "to_state": entry["to_state"],
        "rule_fired": entry["rule_fired"],
        "rule_version": entry["rule_version"],
        "timestamp": entry["timestamp"],
        "actor": entry["actor"],
        "sequence_id": entry["sequence_id"],
        "prev_hash": entry["prev_hash"],
    }, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def verify_chain(audit: list[dict]) -> bool:
    """Verify hash chain of audit entries."""
    prev_hash = None
    for entry in audit:
        # Ensure prev_hash matches expected
        if entry["prev_hash"] != prev_hash:
            print(f"CHAIN BREAK at sequence {entry['sequence_id']}: "
                  f"stored prev {entry['prev_hash']}, expected {prev_hash}")
            return False
        # Recompute hash
        expected = compute_hash(entry)
        if entry.get("entry_hash") != expected:
            print(f"HASH MISMATCH at sequence {entry['sequence_id']}: "
                  f"stored {entry.get('entry_hash')}, computed {expected}")
            return False
        prev_hash = entry["entry_hash"]
    print("AUDIT CHAIN VALID")
    return True


def main():
    if len(sys.argv) != 2:
        print("Usage: python verify_chain.py <audit_export.json>")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        data = json.load(f)

    if "audit" not in data:
        print("JSON must contain 'audit' list")
        sys.exit(1)

    valid = verify_chain(data["audit"])
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    main()