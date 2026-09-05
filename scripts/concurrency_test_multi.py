#!/usr/bin/env python3
"""Concurrent execute-retry across multiple independent workers (no shared memory)."""

import asyncio
import random
import sys
import httpx

PORTS = [8001, 8002, 8003]

async def execute(case_id, client, ports):
    port = random.choice(ports)
    resp = await client.post(f"http://127.0.0.1:{port}/admin/execute-retry/{case_id}")
    return resp.status_code

async def main():
    case_id = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[execute(case_id, client, PORTS) for _ in range(n)],
            return_exceptions=True,
        )
    statuses = [r for r in results if not isinstance(r, Exception)]
    success = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 409)
    print(f"Success: {success}, Rejected: {rejected}")
    assert success == 1 and rejected == n - 1, "Expected exactly one success"
    print("PASS: exactly-once across independent workers confirmed")

if __name__ == "__main__":
    asyncio.run(main())