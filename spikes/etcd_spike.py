#!/usr/bin/env python3
"""
Hour-0 etcd spike — validates that the etcd3 Python client can connect to
a locally running etcd cluster (v3.5.9) and perform basic KV + watch ops.

Usage:
    python spikes/etcd_spike.py [host] [port]

Default: localhost:2379
Exits 0 on success, 1 on failure.
"""

import sys
import threading
import time

import etcd3


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 2379

    print(f"Connecting to etcd at {host}:{port}...")
    try:
        client = etcd3.Etcd3Client(host=host, port=port, timeout=5)
    except Exception as exc:
        print(f"FAIL: could not create etcd3 client: {exc}")
        return 1

    key = "spike/hello"
    value = "world"

    try:
        # PUT
        client.put(key, value)

        # GET
        result = client.get(key)
        if result[0] is None:
            print("FAIL: key not found after put")
            return 1
        if result[0].decode() != value:
            print(f"FAIL: value mismatch: got {result[0].decode()!r}, expected {value!r}")
            return 1

        # WATCH: start a watcher, then update the key in a thread.
        watcher = client.watch(key)

        def update():
            time.sleep(0.5)
            client.put(key, "world-updated")

        t = threading.Thread(target=update)
        t.start()
        for event in watcher:
            if event is not None:
                # etcd3 watch events have a .value attribute; Pylance may not infer it.
                value_bytes = getattr(event, "value", b"")
                print(f"WATCH saw update -> {value_bytes.decode()}")
                break
        t.join(timeout=2)

        print("Spike passed: put/get/watch all worked against this etcd.")
        return 0
    except Exception as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())