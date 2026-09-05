#!/usr/bin/env python3
"""Run multiple etcd fault shapes and print cluster status after each."""

import os
import subprocess
import time
import requests

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8001")


def get_status():
    try:
        r = requests.get(f"{BASE}/cluster/status", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def kill_leader():
    try:
        r = requests.post(f"{BASE}/admin/chaos/kill-leader", timeout=15)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    print("=== Kill follower ===")
    subprocess.run(["docker", "compose", "stop", "etcd3"], check=True)
    time.sleep(12)
    print(get_status())
    subprocess.run(["docker", "compose", "start", "etcd3"], check=True)
    time.sleep(12)
    print()

    print("=== Kill leader (first) ===")
    print(kill_leader())
    time.sleep(12)
    print(get_status())
    subprocess.run(["docker", "compose", "start", "etcd1", "etcd2", "etcd3"], check=True)
    time.sleep(12)

    print("=== Kill leader (second) ===")
    print(kill_leader())
    time.sleep(12)
    print(get_status())
    subprocess.run(["docker", "compose", "start", "etcd1", "etcd2", "etcd3"], check=True)
    time.sleep(12)

    print("=== Restart entire cluster ===")
    subprocess.run(["docker", "compose", "restart", "etcd1", "etcd2", "etcd3"], check=True)
    time.sleep(20)
    print(get_status())


if __name__ == "__main__":
    main()