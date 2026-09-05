#!/bin/bash
# Run different etcd fault shapes and check cluster health.
# Targets worker on port 8001.

set -e

echo "=== Kill follower ==="
docker compose stop etcd3
sleep 5
curl -s http://127.0.0.1:8001/cluster/status | python -m json.tool
docker compose start etcd3
sleep 5
echo

echo "=== Kill leader (twice) ==="
curl -s -X POST http://127.0.0.1:8001/admin/chaos/kill-leader | python -m json.tool
sleep 5
curl -s http://127.0.0.1:8001/cluster/status | python -m json.tool
docker compose start etcd1 etcd2 etcd3
sleep 5
curl -s -X POST http://127.0.0.1:8001/admin/chaos/kill-leader | python -m json.tool
sleep 5
curl -s http://127.0.0.1:8001/cluster/status | python -m json.tool
echo

echo "=== Restart entire cluster ==="
docker compose restart etcd1 etcd2 etcd3
sleep 10
curl -s http://127.0.0.1:8001/cluster/status | python -m json.tool