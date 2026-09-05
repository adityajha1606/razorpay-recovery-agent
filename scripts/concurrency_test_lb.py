#!/usr/bin/env python3
"""Concurrent execute-retry against load balancer (multiple workers)."""

import asyncio
import sys
import httpx

BASE_URL = "http://localhost:8000"

async def execute(case_id, client):
    resp = await client.post(f"/admin/execute-retry/{case_id}")
    return resp.status_code

async def main():
    case_id = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        results = await asyncio.gather(
            *[execute(case_id, client) for _ in range(n)],
            return_exceptions=True,
        )
    statuses = [r for r in results if not isinstance(r, Exception)]
    success = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 409)
    print(f"Success: {success}, Rejected: {rejected}")
    assert success == 1 and rejected == n-1, "Expected exactly one success"
    print("PASS: exactly-once across independent workers confirmed")

if __name__ == "__main__":
    asyncio.run(main())