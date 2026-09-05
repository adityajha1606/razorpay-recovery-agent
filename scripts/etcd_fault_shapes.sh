#!/bin/bash
# Run different etcd fault shapes and check cluster health.

set -e

echo "=== Kill follower ==="
docker compose stop etcd3
sleep 5
curl -s http://localhost:8000/cluster/status | jq .
docker compose start etcd3
sleep 5
echo

echo "=== Kill leader (twice) ==="
curl -s -X POST http://localhost:8000/admin/chaos/kill-leader | jq .
sleep 5
curl -s http://localhost:8000/cluster/status | jq .
docker compose start etcd1 etcd2 etcd3
sleep 5
curl -s -X POST http://localhost:8000/admin/chaos/kill-leader | jq .
sleep 5
curl -s http://localhost:8000/cluster/status | jq .
echo

echo "=== Restart entire cluster ==="
docker compose restart etcd1 etcd2 etcd3
sleep 10
curl -s http://localhost:8000/cluster/status | jq .