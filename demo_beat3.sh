#!/bin/bash
set -u
BASE="http://127.0.0.1:8001"
echo "=== ESCROW Demo: Live recovery flow ==="
for i in $(seq 1 30); do
  CID="demo_treat_$(date +%s)_$i"
  RESP=$(curl -s -X POST "$BASE/admin/simulate-failure" \
    -H 'Content-Type: application/json' \
    -d "{\"payment_id\":\"$CID\",\"vpa\":\"user@upi\",\"amount_paise\":50000,\"currency\":\"INR\",\"error_code\":\"upi_timeout\",\"error_reason\":\"timeout\",\"notes\":{\"mandate_id\":\"MANDATE123\"}}")
  STATE=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','ERROR'))" 2>/dev/null)
  CASE_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('case_id',''))" 2>/dev/null)
  echo "Attempt $i: $CID -> $STATE"
  if [ "$STATE" = "NOTICE_PENDING" ]; then
    echo "Found treatment case: $CASE_ID"
    break
  fi
done
if [ -z "${CASE_ID:-}" ]; then
  echo "ERROR: could not create treatment case."
  exit 1
fi
echo "Waiting 6 seconds so dashboard can update…"
sleep 6
NOTICE_RESPONSE=$(curl -s -X POST "$BASE/admin/send-notice/$CASE_ID")
echo "send-notice response: $NOTICE_RESPONSE"
if echo "$NOTICE_RESPONSE" | grep -q "DND hours active"; then
  echo "DND hours active. Advancing clock by 12 hours…"
  curl -s -X POST "$BASE/admin/advance-clock" \
    -H 'Content-Type: application/json' \
    -d '{"hours": 12}' > /dev/null
  sleep 2
  echo "Retrying send-notice…"
  NOTICE_RESPONSE=$(curl -s -X POST "$BASE/admin/send-notice/$CASE_ID")
  echo "send-notice response after clock advance: $NOTICE_RESPONSE"
fi
echo "Waiting 6 seconds to show state change on dashboard…"
sleep 6
RECEIPT_URL="$BASE/cases/$CASE_ID/receipt"
echo "Receipt URL: $RECEIPT_URL"
if command -v explorer.exe > /dev/null 2>&1; then
  explorer.exe "$RECEIPT_URL"
elif command -v xdg-open > /dev/null 2>&1; then
  xdg-open "$RECEIPT_URL"
fi
echo "Demo flow complete."
