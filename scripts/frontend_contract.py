#!/usr/bin/env python3
"""Exercise the API exactly as the React client calls it.

`smoke.sh` proves the two business flows work. This proves the *frontend agrees
with the backend about how to ask*: every request below mirrors a real call in
Online_Marketplace_4_Construction/src/services/*.js - same path, same query
parameters, same body shape - so a drifted parameter shows up here rather than
as a 422 in someone's browser console.

It caught a real one: the sort dropdown sent `name_asc`, which the products
endpoint does not accept, and the whole catalogue 422'd.

Run against a seeded API:

    python3 scripts/frontend_contract.py

Only stdlib, so it needs no virtualenv.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
BASE = f"{BASE}/api/v1"
PASSWORD = "DemoPass!2026"

failures: list[str] = []
checks = 0


def call(method, path, *, token=None, body=None, params=None, expect=200):
    global checks
    checks += 1
    url = f"{BASE}{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)

    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read()

    parsed = json.loads(payload) if payload else None
    if status != expect:
        failures.append(f"{method} {path} -> {status}, expected {expect}: {parsed}")
        return None
    print(f"  ok  {method} {path} [{status}]")
    return parsed


def login(email):
    data = call("POST", "/auth/login", body={"email": email, "password": PASSWORD})
    if not data:
        sys.exit(f"cannot log in as {email}")
    return data["access_token"], data["user"]


print("\n== auth ==")
client_token, client_user = login("client@demo.com")
vendor_token, _ = login("vendor@demo.com")
worker_token, worker_user = login("worker@demo.com")
call("POST", "/auth/login", body={"email": "client@demo.com", "password": "wrong"}, expect=401)

print("\n== catalogue (Materials.jsx / Home.jsx) ==")
categories = call("GET", "/categories")
# The four sorts the sort dropdown now offers, plus relevance for a keyword search.
for sort in ("newest", "price_asc", "price_desc", "name"):
    call("GET", "/products", params={"sort": sort, "page": 1, "size": 12})
call("GET", "/products", params={"q": "cement", "sort": "relevance", "page": 1, "size": 12})
top_level = [c for c in categories if c.get("parent_id") is None]
call(
    "GET",
    "/products",
    params={
        "category_id": top_level[0]["id"],
        "min_price": 0,
        "max_price": 50,
        "sort": "newest",
        "page": 1,
        "size": 12,
    },
)
products = call("GET", "/products", params={"size": 24})
in_stock = next(p for p in products["items"] if p["stock_qty"] > 2)
call("GET", f"/products/{in_stock['id']}")

print("\n== workers (Workers.jsx) ==")
skills = call("GET", "/skills")
for sort in ("rating", "experience", "rate_asc", "newest"):
    call("GET", "/workers", params={"sort": sort, "page": 1, "size": 12})
call(
    "GET",
    "/workers",
    params={
        "skill": skills[0]["slug"],
        "min_rating": 3,
        "availability": "AVAILABLE",
        "sort": "rating",
        "page": 1,
        "size": 12,
    },
)
worker_id = worker_user["id"]
call("GET", f"/workers/{worker_id}")
call("GET", f"/workers/{worker_id}/reviews", params={"size": 20})

print("\n== profile (Profile.jsx) ==")
call("GET", "/users/me", token=client_token)
call("PATCH", "/users/me", token=client_token, body={"phone": "0244000111", "city": "Accra"})
call("GET", "/users/me/vendor-profile", token=vendor_token)

print("\n== cart + checkout (CartContext / Cart.jsx) ==")
call("GET", "/cart", token=client_token)
cart = call(
    "POST",
    "/cart/items",
    token=client_token,
    body={"product_id": in_stock["id"], "quantity": 2},
    expect=201,
)
line_id = cart["items"][-1]["id"]
call("PATCH", f"/cart/items/{line_id}", token=client_token, body={"quantity": 1})
order = call(
    "POST",
    "/orders",
    token=client_token,
    body={"delivery_address": "12 Independence Ave, Accra", "contact_phone": "0244000111"},
    expect=201,
)
call("GET", "/orders", token=client_token, params={"page": 1, "size": 50})
call("GET", f"/orders/{order['id']}", token=client_token)

print("\n== vendor queue (VendorDashboard.jsx) ==")
call("GET", "/vendor/products", token=vendor_token, params={"page": 1, "size": 50})
queue = call("GET", "/vendor/orders", token=vendor_token, params={"page": 1, "size": 50})
mine = [line for line in queue["items"] if line["order_id"] == order["id"]]
if mine:
    call(
        "PATCH",
        f"/vendor/orders/items/{mine[0]['id']}",
        token=vendor_token,
        body={"vendor_status": "CONFIRMED"},
    )
else:
    failures.append("vendor queue did not contain the order just placed")

print("\n== jobs (HireWorker / dashboards) ==")
job = call(
    "POST",
    "/jobs",
    token=client_token,
    body={
        "worker_id": worker_id,
        "title": "Wire the ground floor",
        "description": "Full first-fix wiring for a three bedroom ground floor slab.",
        "location": "Accra",
        "budget": "2500",
    },
    expect=201,
)
call("GET", "/jobs", token=client_token, params={"role": "sent", "page": 1, "size": 20})
call("GET", "/jobs", token=worker_token, params={"role": "received", "page": 1, "size": 20})
call("PATCH", f"/jobs/{job['id']}/status", token=worker_token, body={"status": "ACCEPTED"})
call("PATCH", f"/jobs/{job['id']}/status", token=worker_token, body={"status": "IN_PROGRESS"})
call("PATCH", f"/jobs/{job['id']}/status", token=client_token, body={"status": "COMPLETED"})
call(
    "POST",
    f"/jobs/{job['id']}/review",
    token=client_token,
    body={"rating": 5, "comment": "Neat work, finished a day early."},
    expect=201,
)

print("\n== notifications (Navbar badge / Notifications.jsx) ==")
call("GET", "/notifications", token=worker_token, params={"page": 1, "size": 20})
unread = call("GET", "/notifications/unread-count", token=worker_token)
feed = call("GET", "/notifications", token=worker_token, params={"unread_only": "true", "size": 20})
if feed and feed["items"]:
    call("PATCH", f"/notifications/{feed['items'][0]['id']}/read", token=worker_token)
call("PATCH", "/notifications/read-all", token=worker_token)

print("\n== authorization boundaries ==")
call("GET", "/cart", token=vendor_token, expect=403)          # vendor has no cart
call("GET", "/vendor/orders", token=client_token, expect=403)  # client is not a vendor
call("GET", "/workers/me", token=client_token, expect=403)     # client has no worker profile
call("GET", "/users/me", expect=401)                           # no token at all
# Re-sending the status a job already has is a deliberate no-op, not an error.
call("PATCH", f"/jobs/{job['id']}/status", token=worker_token, body={"status": "COMPLETED"})
# A genuinely illegal move is refused by the state machine.
call(
    "PATCH",
    f"/jobs/{job['id']}/status",
    token=worker_token,
    body={"status": "ACCEPTED"},
    expect=409,
)

# A second client must not be able to see or touch someone else's job/order.
other = call(
    "POST",
    "/auth/register",
    body={
        "email": f"probe{order['id']}@demo.com",
        "password": "ProbeAccount!2026",
        "full_name": "Probe Account",
        "role": "CLIENT",
    },
    expect=201,
)
if other:
    probe = other["access_token"]
    call("GET", f"/orders/{order['id']}", token=probe, expect=404)
    call("GET", f"/jobs/{job['id']}", token=probe, expect=404)
    call(
        "PATCH",
        f"/cart/items/{line_id}",
        token=probe,
        body={"quantity": 5},
        expect=404,
    )

print("\n" + "=" * 60)
if failures:
    print(f"FAILED {len(failures)} of {checks} checks:")
    for failure in failures:
        print(f"  - {failure}")
    sys.exit(1)
print(f"All {checks} checks passed against {BASE}.")
