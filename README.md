# Buildify Backend

Backend API for **Buildify**, a marketplace for construction materials and
skilled workers.

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · MySQL 8 · Alembic · Cloudflare R2

The full design rationale is in [`../BACKEND_ARCHITECTURE.md`](../BACKEND_ARCHITECTURE.md).

---

## Quick start (Docker — recommended)

You need only Docker Desktop; Python and MySQL run inside containers.

```bash
cd backend
cp .env.example .env                       # then edit JWT_SECRET_KEY and the R2_* values
docker compose up -d --build
docker compose exec api alembic upgrade head
docker compose exec api python -m app.seeds.seed
open http://localhost:8000/docs
```

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000 | `/docs` for interactive OpenAPI, `/health` for the probe |
| Adminer | http://localhost:8080 | database GUI; server `mysql`, user `marketplace` |
| MySQL | `localhost:3307` | port 3307 so a locally installed MySQL is left alone |

## Quick start (native)

```bash
brew install python@3.12 mysql
brew services start mysql
mysql -u root -e "CREATE DATABASE marketplace; CREATE DATABASE marketplace_test;
  CREATE USER 'marketplace'@'localhost' IDENTIFIED BY 'marketplace';
  GRANT ALL ON marketplace.* TO 'marketplace'@'localhost';
  GRANT ALL ON marketplace_test.* TO 'marketplace'@'localhost';"

cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env      # set MYSQL_HOST=127.0.0.1
alembic upgrade head
python -m app.seeds.seed
uvicorn app.main:app --reload
```

---

## Demo accounts

Created by the seed script. Password for all of them: `DemoPass!2026`

| Email | Role | Use |
|---|---|---|
| `admin@demo.com` | ADMIN | manage categories and the skills list |
| `client@demo.com` | CLIENT | browse, cart, checkout, hire workers |
| `vendor@demo.com` | VENDOR | product listings, order queue |
| `worker@demo.com` | WORKER | profile, job requests |

Also seeded: a second vendor, two more workers, 6 top-level categories with
sub-categories, 8 skills, and 13 products with realistic Ghanaian pricing.

---

## Authentication

```bash
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"vendor@demo.com","password":"DemoPass!2026"}'
```

Send the returned `access_token` as `Authorization: Bearer <token>` (30 minute
lifetime). When it expires, `POST /api/v1/auth/refresh` with the refresh token —
which **rotates**: the old refresh token is revoked on use, so a stolen one is
good for at most a single call.

---

## API surface

All routes are prefixed `/api/v1`. Full generated reference at `/docs`.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/{register,login,refresh,logout}` |
| Account | `GET,PATCH /users/me` · `GET,PATCH /users/me/vendor-profile` |
| Categories | `GET /categories` · `POST /categories` *(admin)* |
| Browse | `GET /products?q=&category_id=&min_price=&max_price=&region=&sort=` · `GET /products/{id}` |
| Vendor products | `GET /vendor/products` · `POST /products` · `PATCH,DELETE /products/{id}` · `POST /products/{id}/images` |
| Cart | `GET,DELETE /cart` · `POST /cart/items` · `PATCH,DELETE /cart/items/{id}` |
| Orders | `POST /orders` · `GET /orders` · `GET /orders/{id}` · `POST /orders/{id}/cancel` |
| Vendor queue | `GET /vendor/orders` · `PATCH /vendor/orders/items/{id}` |
| Skills | `GET /skills` · `POST /skills` *(admin)* |
| Workers | `GET /workers?skill=&region=&city=&min_rating=` · `GET /workers/{id}` · `GET,PATCH /workers/me` · `PUT /workers/me/{skills,portfolio}` |
| Jobs | `POST /jobs` · `GET /jobs?role=sent\|received` · `GET /jobs/{id}` · `PATCH /jobs/{id}/status` |
| Reviews | `POST /jobs/{id}/review` · `GET /workers/{id}/reviews` · `GET /workers/{id}/rating` |
| Media | `POST /media/upload-url` |
| Notifications | `GET /notifications` · `GET /notifications/unread-count` · `PATCH /notifications/{id}/read` |

### Postman

Import `http://localhost:8000/openapi.json` directly — Postman generates the
whole collection from it, so there is no collection file to keep in sync.

---

## Uploading an image

Files go straight from the browser to Cloudflare R2; they never pass through
this API.

```bash
# 1. ask for a presigned URL (as a vendor)
curl -X POST http://localhost:8000/api/v1/media/upload-url \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"purpose":"product","content_type":"image/jpeg"}'

# 2. PUT the bytes to the returned upload_url
curl -X PUT "$UPLOAD_URL" -H 'Content-Type: image/jpeg' --data-binary @cement.jpg

# 3. attach the object_key to the product
curl -X POST http://localhost:8000/api/v1/products/1/images \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"object_key":"product/2/abc123.jpg","sort_order":0}'
```

Step 3 verifies the key is under the caller's own prefix **and** that the object
really exists in the bucket before storing it. Set R2 CORS to allow `PUT` from
your frontend origin, or step 2 fails in the browser.

---

## Tests

```bash
docker compose exec api pytest -v          # Docker
pytest -v                                  # native, venv activated
```

The suite runs against a separate `marketplace_test` schema (created by
`docker/init/01-create-test-db.sql` on first container boot), and truncates every
table between tests.

### End-to-end smoke test

Drives the two flows you'll demo, over real HTTP, against a running and seeded API:

```bash
./scripts/smoke.sh                      # defaults to http://localhost:8000
./scripts/smoke.sh https://your-app.up.railway.app
```

Worth running once before your defence — it checks the happy paths *and* that the
refusals still refuse (`PENDING → READY` on an order line, a worker trying to
complete their own job, a second review on the same job).

### Frontend contract check

```bash
python3 scripts/frontend_contract.py                    # defaults to localhost:8000
python3 scripts/frontend_contract.py https://your-app.up.railway.app
```

Replays every call the React client makes — same paths, same query parameters,
same request bodies as `src/services/*.js` — plus the refusals the interface
relies on (a vendor has no cart, a client has no vendor queue, another client
gets a 404 on your order, your job and your cart line). Where `smoke.sh` asks
"does the flow work", this asks "do the two halves still agree how to ask".

### Editor setup

The imports only resolve if your editor uses the project's virtualenv — the
system Python has none of these packages installed:

```bash
brew install python@3.12
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

`.vscode/settings.json` already points VS Code at `backend/.venv/bin/python`.
Reload the window (⇧⌘P → *Developer: Reload Window*) if warnings linger.
Lint with `.venv/bin/ruff check app tests`.

Notable cases:

- a vendor cannot edit another vendor's product → `403`
- checkout with insufficient stock rolls back; stock is untouched, no order written
- **two concurrent checkouts of the last unit: exactly one wins** and stock never
  goes negative — the test that justifies `SELECT ... FOR UPDATE` in `checkout`
- every illegal job transition → `409`; a worker cannot mark their own job complete
- reviewing an unfinished job, or the same job twice, is rejected
- `avg_rating` equals the arithmetic mean of the reviews behind it

---

## Project layout

```
app/
  core/        config, enums, security, deps (RBAC), errors, pagination, logging
  db/          declarative base, async session, model registry
  modules/
    auth/      register, login, refresh, logout
    users/     User, VendorProfile
    catalog/   Category, Product, ProductImage
    orders/    Cart, Order, OrderItem  <- checkout lives here
    workers/   WorkerProfile, Skill, WorkerSkill
    jobs/      JobRequest + transition table
    reviews/   Review + rating aggregation
    media/     Cloudflare R2
    notifications/
  seeds/       demo data
alembic/       migrations
tests/
```

Each module owns `models.py`, `schemas.py`, `service.py`, `router.py`. Routers
validate and delegate; **business rules and ownership checks live in services**.

---

## Migrations

```bash
docker compose exec api alembic revision --autogenerate -m "add supplier notes"
docker compose exec api alembic upgrade head
docker compose exec api alembic downgrade -1
```

New model modules must be imported in `app/db/registry.py`, or autogenerate will
not see them and will produce an empty migration.

---

## Deployment

The image is host-agnostic — Railway, Render and Fly.io all work.

1. Provision managed MySQL 8; set `MYSQL_HOST/PORT/USER/PASSWORD/DATABASE`.
2. Set `ENVIRONMENT=production`, `DEBUG=false`, and a strong `JWT_SECRET_KEY`:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
3. Set `CORS_ORIGINS` to your deployed frontend origin (no wildcard).
4. Set the `R2_*` variables and allow `PUT` from that origin in R2's CORS rules.
5. Run `alembic upgrade head` as a release command.
6. Point the health check at `/health`.

`ENVIRONMENT=production` is load-bearing beyond the name. It makes the app refuse
to boot on a placeholder or short `JWT_SECRET_KEY`, with `DEBUG=true`, or with a
wildcard in `CORS_ORIGINS`; it hides `/docs`, `/redoc` and `/openapi.json`; and
it decides whether `X-Forwarded-For` is believed.

**Rate limiting is on in every environment**, not only production — a control
that first runs on the day it is needed is not a control. Only the test suite
disables it (`RATE_LIMIT_ENABLED=false` in `tests/conftest.py`), because the
suite logs in far more often than a person would.

### Proxies and the rate limiter

`TRUST_PROXY_HEADERS` decides whether the limiter keys on `X-Forwarded-For`
instead of the socket peer. Leave it unset: it then follows `ENVIRONMENT`.

- **In production it is on**, because every supported host terminates TLS at a
  proxy. Without it every request would appear to come from that proxy and share
  a single bucket, throttling real users and letting an attacker hide in the
  crowd.
- **Everywhere else it is off**, because nothing rewrites the header locally.
  Believing it there hands out an unlimited number of login attempts: send a
  different `X-Forwarded-For` each time and every request lands in a fresh
  bucket.

Set it explicitly (`TRUST_PROXY_HEADERS=true|false`) only if your topology
differs from that — for instance a production deployment reachable directly,
without a proxy in front.

---

## Design notes

- **No payment gateway.** The proposal excludes online payments; an order is a
  confirmed request to a vendor, settled offline. `orders.status`/`subtotal` leave
  a clean insertion point for future work.
- **Money is `DECIMAL(12,2)`**, never float.
- **Products are archived, never deleted** — otherwise `order_items` would be
  orphaned and order history destroyed.
- **`order_items` carries `vendor_id` and `vendor_status`** so one order can span
  multiple vendors, each acting only on their own lines.
- **Ratings are denormalised** onto `worker_profiles` for fast sorting, recomputed
  inside the same transaction that writes the review.
- **Search uses MySQL full-text**, with a `LIKE` fallback for terms shorter than
  `innodb_ft_min_token_size` (the compose file lowers it to 2).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Can't connect to MySQL` | Native run: set `MYSQL_HOST=127.0.0.1`. In Docker it must be `mysql`. |
| `pytest` cannot find `marketplace_test` | The init SQL only runs on a fresh volume: `docker compose down -v && docker compose up -d` |
| `storage_unconfigured` on upload | The `R2_*` variables are blank in `.env`. |
| Search returns nothing for a short word | Below the full-text token size; the `LIKE` fallback needs a prefix match. |
| `asyncmy` fails to build natively | `pip install aiomysql` and change the driver in `Settings.database_url`. |
