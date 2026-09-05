#!/usr/bin/env python3
"""
Load test hitting /admin/execute-retry (real path with etcd/Postgres).

Usage:
    BASE_URL=http://127.0.0.1:8001 python scripts/load_test_execute.py <case_id> [num_requests] [concurrency]
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8001")
TOKEN = os.getenv("ADMIN_TOKEN")  # optional


def execute(case_id: str):
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    return requests.post(f"{BASE_URL}/admin/execute-retry/{case_id}", headers=headers).status_code


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/load_test_execute.py <case_id> [num_requests] [concurrency]")
        sys.exit(1)

    case_id = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    concurrency = int(sys.argv[3]) if len(sys.argv) > 3 else 20

    print(f"Load test on {BASE_URL}/admin/execute-retry for {case_id}: {n} req, concurrency {concurrency}")
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(execute, case_id) for _ in range(n)]
        statuses = [f.result() for f in as_completed(futures)]
    elapsed = time.monotonic() - start
    req_per_sec = n / elapsed
    print(f"Done in {elapsed:.2f}s, {req_per_sec:.1f} req/s")
    print(f"Status counts: { {s: statuses.count(s) for s in set(statuses)} }")


if __name__ == "__main__":
    main()