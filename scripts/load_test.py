#!/usr/bin/env python3
"""
Simple load test for the recovery agent API.

Usage:
    python scripts/load_test.py [num_requests] [concurrency]

Defaults: num_requests=1000, concurrency=20

Hits POST /admin/simulate-failure with synthetic data and reports req/s.
Run the server first:
    export CONFIG_PROFILE=demo
    export RAZORPAY_WEBHOOK_SECRET=test_secret_123
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://localhost:8000"


def send_failure(i: int) -> tuple[int, bool, str]:
    payload = {
        "payment_id": f"pay_load_{i}",
        "amount_paise": 50000,
        "vpa": f"loaduser{i}@upi",
        "error_code": "bank_server_down",
        "error_reason": "load_test",
        "notes": {"mandate_id": f"mandate_load_{i}"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/admin/simulate-failure",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return i, True, resp.status
    except Exception as exc:
        return i, False, str(exc)


def main():
    num_requests = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    print(f"Starting load test: {num_requests} requests, concurrency={concurrency}")
    start = time.monotonic()
    success = 0
    failure = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_failure, i) for i in range(num_requests)]
        for future in as_completed(futures):
            _, ok, _ = future.result()
            if ok:
                success += 1
            else:
                failure += 1

    elapsed = time.monotonic() - start
    req_per_sec = num_requests / elapsed if elapsed > 0 else 0

    print(f"\nResults:")
    print(f"  Success: {success}")
    print(f"  Failure: {failure}")
    print(f"  Total time: {elapsed:.2f}s")
    print(f"  Throughput: {req_per_sec:.1f} req/s")


if __name__ == "__main__":
    main()