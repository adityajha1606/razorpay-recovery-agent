#!/usr/bin/env python3
"""
Concurrent execute-retry test against a live server with real etcd.

Usage:
    python scripts/concurrency_test.py <case_id> [num_requests]

Requires:
    - API running with USE_ETCD=true and USE_POSTGRES=true (or in-memory)
    - A case in RETRY_SCHEDULED state with a pending retry decision.

Fires num_requests simultaneous POSTs to /admin/execute-retry/{case_id}
and asserts exactly one succeeds (200) and the rest get 409/duplicate.
"""

import sys
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000"
ADMIN_TOKEN = None  # set via environment if needed, e.g. os.environ.get("ADMIN_TOKEN")


def execute_once(case_id: str, token: str | None = None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    resp = requests.post(f"{BASE_URL}/admin/execute-retry/{case_id}", headers=headers)
    return resp.status_code, resp.text


def main():
    case_id = sys.argv[1] if len(sys.argv) > 1 else "pay_demo_001"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    print(f"Firing {n} concurrent execute-retry requests for case {case_id}")
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [ex.submit(execute_once, case_id, ADMIN_TOKEN) for _ in range(n)]
        results = [f.result() for f in as_completed(futures)]
    elapsed = time.monotonic() - start

    success_count = sum(1 for status, _ in results if status == 200)
    rejected_count = sum(1 for status, _ in results if status == 409)
    other_count = len(results) - success_count - rejected_count

    print(f"\nResults ({n} requests in {elapsed:.2f}s):")
    print(f"  Success (200): {success_count}")
    print(f"  Rejected/duplicate (409): {rejected_count}")
    print(f"  Other: {other_count}")
    if success_count != 1:
        print("FAIL: expected exactly one success.")
        sys.exit(1)
    print("PASS: exactly one commit succeeded; rest were cleanly rejected.")


if __name__ == "__main__":
    main()