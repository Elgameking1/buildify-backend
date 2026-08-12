#!/usr/bin/env bash
# End-to-end smoke test of the two flows to demo at defence.
#
# Exercises the running API over HTTP with the seeded demo accounts:
#   1. Materials  - vendor lists -> client searches -> cart -> checkout ->
#                   vendor fulfils -> order rolls up to FULFILLED
#   2. Hiring     - client searches by skill+location -> job request ->
#                   accept -> in progress -> complete -> review -> rating
#
# Usage:  ./scripts/smoke.sh [base_url]
# Needs:  a running API that has been migrated and seeded, plus jq.

set -euo pipefail

BASE="${1:-http://localhost:8000}"
API="$BASE/api/v1"
PASSWORD="DemoPass!2026"

command -v jq >/dev/null || { echo "jq is required (brew install jq)"; exit 1; }

pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

expect() { # expect <actual> <wanted> <label>
  if [ "$1" = "$2" ]; then pass "$3 ($1)"; else fail "$3: got '$1', wanted '$2'"; fi
}

login() { # login <email> -> access token
  curl -sS -X POST "$API/auth/login" -H 'Content-Type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$PASSWORD\"}" | jq -r '.access_token'
}

get()   { curl -sS "$API$1" -H "Authorization: Bearer $2"; }
post()  { curl -sS -X POST "$API$1" -H "Authorization: Bearer $2" \
            -H 'Content-Type: application/json' -d "$3"; }
patch() { curl -sS -X PATCH "$API$1" -H "Authorization: Bearer $2" \
            -H 'Content-Type: application/json' -d "$3"; }

step "Health"
health=$(curl -sS "$BASE/health")
expect "$(jq -r '.status'   <<<"$health")" "ok" "status"
expect "$(jq -r '.database' <<<"$health")" "up" "database reachable"

step "Authentication"
CLIENT=$(login client@demo.com)
VENDOR=$(login vendor@demo.com)
WORKER=$(login worker@demo.com)
[ -n "$CLIENT" ] && [ "$CLIENT" != "null" ] || fail "client login"
pass "logged in as client, vendor and worker"

# ---------------------------------------------------------------- flow 1 -----
step "Flow 1: materials marketplace"

search=$(curl -sS "$API/products?q=cement&sort=price_asc")
found=$(jq -r '.total' <<<"$search")
[ "$found" -gt 0 ] || fail "keyword search for 'cement' returned nothing"
pass "keyword search found $found cement product(s)"

# Buy something this vendor actually sells: the seed spreads products across
# two vendors, so picking off the public listing may land on the other one and
# the line would then correctly not appear in this vendor's queue.
mine=$(get "/vendor/products" "$VENDOR")
picked=$(jq -c '[.items[] | select(.status=="ACTIVE" and .stock_qty >= 3)][0]' <<<"$mine")
[ "$picked" != "null" ] || fail "the demo vendor has no stocked, active product"
PRODUCT_ID=$(jq -r '.id' <<<"$picked")
PRODUCT_NAME=$(jq -r '.name' <<<"$picked")
STOCK_BEFORE=$(jq -r '.stock_qty' <<<"$picked")
pass "picked the vendor's own '$PRODUCT_NAME' (stock $STOCK_BEFORE)"

cart=$(post "/cart/items" "$CLIENT" "{\"product_id\":$PRODUCT_ID,\"quantity\":3}")
expect "$(jq -r '.item_count' <<<"$cart")" "3" "cart item count"

order=$(post "/orders" "$CLIENT" \
  '{"delivery_address":"12 Independence Avenue, Accra","contact_phone":"0244000001"}')
ORDER_ID=$(jq -r '.id' <<<"$order")
ORDER_NO=$(jq -r '.order_number' <<<"$order")
expect "$(jq -r '.status' <<<"$order")" "PENDING" "new order status"
pass "placed $ORDER_NO, subtotal GHS $(jq -r '.subtotal' <<<"$order")"

stock_after=$(curl -sS "$API/products/$PRODUCT_ID" | jq -r '.stock_qty')
expect "$stock_after" "$((STOCK_BEFORE - 3))" "stock decremented by 3"

emptied=$(get "/cart" "$CLIENT" | jq -r '.item_count')
expect "$emptied" "0" "cart emptied by checkout"

queue=$(get "/vendor/orders" "$VENDOR")
ITEM_ID=$(jq -r --arg n "$ORDER_NO" '.items[] | select(.order_number==$n) | .id' <<<"$queue" | head -1)
[ -n "$ITEM_ID" ] || fail "order line not visible in the vendor queue"
pass "line $ITEM_ID visible in the vendor's queue"

bad=$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH \
  "$API/vendor/orders/items/$ITEM_ID" -H "Authorization: Bearer $VENDOR" \
  -H 'Content-Type: application/json' -d '{"vendor_status":"READY"}')
expect "$bad" "409" "PENDING -> READY rejected (must confirm first)"

patch "/vendor/orders/items/$ITEM_ID" "$VENDOR" '{"vendor_status":"CONFIRMED"}' >/dev/null
expect "$(get "/orders/$ORDER_ID" "$CLIENT" | jq -r '.status')" "CONFIRMED" "order rolled up to CONFIRMED"

patch "/vendor/orders/items/$ITEM_ID" "$VENDOR" '{"vendor_status":"READY"}' >/dev/null
expect "$(get "/orders/$ORDER_ID" "$CLIENT" | jq -r '.status')" "FULFILLED" "order rolled up to FULFILLED"

# ---------------------------------------------------------------- flow 2 -----
step "Flow 2: hiring a skilled worker"

workers=$(curl -sS "$API/workers?skill=masonry&region=Greater%20Accra")
wcount=$(jq -r '.total' <<<"$workers")
[ "$wcount" -gt 0 ] || fail "no masons found in Greater Accra"
WORKER_ID=$(jq -r '.items[0].user_id' <<<"$workers")
pass "found $wcount mason(s) in Greater Accra: $(jq -r '.items[0].full_name' <<<"$workers")"

job=$(post "/jobs" "$CLIENT" "$(cat <<JSON
{"worker_id":$WORKER_ID,
 "title":"Build a boundary wall",
 "description":"Block work for a 30 metre boundary wall in East Legon.",
 "location":"East Legon, Accra",
 "budget":"4500.00"}
JSON
)")
JOB_ID=$(jq -r '.id' <<<"$job")
expect "$(jq -r '.status' <<<"$job")" "PENDING" "new job status"

early=$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH "$API/jobs/$JOB_ID/status" \
  -H "Authorization: Bearer $CLIENT" -H 'Content-Type: application/json' \
  -d '{"status":"COMPLETED"}')
expect "$early" "409" "PENDING -> COMPLETED rejected"

patch "/jobs/$JOB_ID/status" "$WORKER" '{"status":"ACCEPTED"}'    >/dev/null
patch "/jobs/$JOB_ID/status" "$WORKER" '{"status":"IN_PROGRESS"}' >/dev/null
pass "worker accepted and started"

wrong=$(curl -sS -o /dev/null -w '%{http_code}' -X PATCH "$API/jobs/$JOB_ID/status" \
  -H "Authorization: Bearer $WORKER" -H 'Content-Type: application/json' \
  -d '{"status":"COMPLETED"}')
expect "$wrong" "403" "worker cannot mark their own job complete"

patch "/jobs/$JOB_ID/status" "$CLIENT" '{"status":"COMPLETED"}' >/dev/null
pass "client marked the job complete"

review=$(post "/jobs/$JOB_ID/review" "$CLIENT" \
  '{"rating":5,"comment":"Neat work, finished on time."}')
expect "$(jq -r '.rating' <<<"$review")" "5" "review created"

twice=$(curl -sS -o /dev/null -w '%{http_code}' -X POST "$API/jobs/$JOB_ID/review" \
  -H "Authorization: Bearer $CLIENT" -H 'Content-Type: application/json' \
  -d '{"rating":1}')
expect "$twice" "409" "second review on the same job rejected"

rating=$(curl -sS "$API/workers/$WORKER_ID/rating")
[ "$(jq -r '.rating_count' <<<"$rating")" -gt 0 ] || fail "rating_count did not update"
pass "worker rating now $(jq -r '.avg_rating' <<<"$rating") from $(jq -r '.rating_count' <<<"$rating") review(s)"

step "All smoke checks passed"
