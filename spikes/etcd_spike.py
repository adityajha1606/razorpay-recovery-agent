"""
Hour-0 de-risking spike for etcd3 (builder doc §10.1, §14 Q1).

This is throwaway and NOT part of the real app — that's why it lives in
spikes/, outside the app/ package, and imports nothing from app/. Run it
from its own worktree/branch before anyone builds CommitBackend on top of
etcd. The whole point of the hour-20 gate in the builder doc is that etcd is
the least predictable dependency, so prove it works here in the first hour,
not discover it's flaky at hour 18 when it's load-bearing.

Delete this file (or leave it — it's harmless) once the Phase 2 gate has
passed and app/core/commit_backend.py's EtcdQuorumBackend is doing the same
job for real.

Start a single-node etcd just for this spike (the real system is the
3-node docker-compose cluster from §3 / docker-compose.yml, used from
Phase 2 onward):

    docker run -d --name etcd-spike -p 2379:2379 \
        quay.io/coreos/etcd:v3.5.9 \
        etcd --advertise-client-urls http://0.0.0.0:2379 \
             --listen-client-urls http://0.0.0.0:2379

Install the client and run:

    pip install etcd3 --break-system-packages
    python spikes/etcd_spike.py
"""

import etcd3


def main() -> None:
    client = etcd3.client(host="localhost", port=2379)
    key = "spike/hello"

    # 1. Put
    client.put(key, "world")
    print(f"PUT  {key} = world")

    # 2. Get
    value, meta = client.get(key)
    print(f"GET  {key} = {value.decode()} (revision={meta.mod_revision})")

    # 3. Watch — start watching, then fire an update so there's an event
    #    to observe. One event is enough to prove the watch path works.
    events_iterator, cancel = client.watch(key)
    client.put(key, "world-updated")

    for event in events_iterator:
        print(f"WATCH saw update -> {event.value.decode()}")
        break

    cancel()
    print("Spike passed: put/get/watch all worked against this etcd.")


if __name__ == "__main__":
    main()
