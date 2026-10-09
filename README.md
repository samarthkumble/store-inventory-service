# Store Inventory & Replenishment Service

[![CI](https://github.com/samarthkumble/store-inventory-service/actions/workflows/ci.yml/badge.svg)](https://github.com/samarthkumble/store-inventory-service/actions/workflows/ci.yml)

Two REST microservices for a home-improvement retailer's store inventory:

- **Inventory service:** per-store stock levels, shelf locations (aisle and bay), ranked product search, and order reservations that **cannot oversell, even under heavy concurrent load**.
- **Replenishment service:** consumes `StockChanged` events and suggests reorders from a 7-day moving average of demand.

Built with Python 3.12, FastAPI, PostgreSQL 17, SQLAlchemy 2.0, Alembic, Pydantic, Redis Streams, pytest, Docker Compose and GitHub Actions.

## The problem

A store has 10 units of a tap left and 50 customers try to buy one at the same moment. The obvious code (read the stock, check it, write the new value) lets many requests read the same old value, pass the check, and overwrite each other's writes. Customers are promised stock that doesn't exist, and the database still looks perfectly valid.

This service makes the check and the write a single atomic step, and proves it with a test that fires 50 truly simultaneous HTTP requests at a real server and database. The naive version runs through the same test to show the bug actually happening.

## Architecture

```mermaid
flowchart LR
    client["Client<br/>(app, POS, /docs)"] -->|HTTP JSON| api

    subgraph inv["Inventory service :8000"]
        api["FastAPI<br/>routers → services"]
    end

    api -->|"one transaction:<br/>stock change + outbox row"| db[("PostgreSQL 17<br/>stock, orders, outbox")]
    api <-->|"cache-aside<br/>product lookups"| redis[("Redis")]
    relay["Outbox relay"] -->|"poll unpublished rows<br/>(SKIP LOCKED)"| db
    relay -->|"XADD StockChanged"| stream[["Redis Stream<br/>stock-events"]]
    stream -->|"XREADGROUP<br/>consumer group"| consumer["Replenishment consumer<br/>idempotent by event_id"]
    consumer --> rdb[("replenishment schema<br/>stock levels, daily demand")]

    subgraph rep["Replenishment service :8001"]
        rapi["GET /reorder-suggestions"]
    end
    rapi --> rdb
```

Order lifecycle (every transition is a conditional `UPDATE ... WHERE status = 'RESERVED'`, so a double-click can't apply it twice):

```mermaid
stateDiagram-v2
    [*] --> RESERVED: POST /orders (reserved += qty)
    RESERVED --> CONFIRMED: confirm (on_hand -= qty, reserved -= qty)
    RESERVED --> CANCELLED: cancel (reserved -= qty)
    CONFIRMED --> [*]
    CANCELLED --> [*]
```

## Run it

Requires Docker Desktop.

```bash
docker compose up -d --build --wait                      # 6 containers, waits until all are healthy
docker compose exec api python -m scripts.seed --reset   # 3 stores, 200 products, 561 stock rows
```

Interactive API docs: **http://localhost:8000/docs** (inventory) and **http://localhost:8001/docs** (replenishment).

| Container | Role |
|---|---|
| `inventory-db` | PostgreSQL 17 |
| `inventory-redis` | Redis 7: event stream and product cache |
| `inventory-api` | Inventory service (runs migrations at start) |
| `inventory-outbox-relay` | Publishes outbox rows to the `stock-events` stream |
| `replenishment-api` | Replenishment service |
| `replenishment-consumer` | Reads the stream, updates demand and stock levels |

See the event pipeline work, timed: `python -m scripts.demo_events`.

### Run the tests

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d db redis
pytest --cov
```

Tests create and migrate their own `inventory_test` database and use Redis database 15, so development data is never touched.

## API

| Method | Path | What it does |
|---|---|---|
| `GET` | `/stores` | List stores |
| `GET` | `/stores/{store_id}/products?q=&category=` | Full-text product search in one store, most relevant first, with availability, aisle and bay |
| `GET` | `/stores/{store_id}/stock/{sku}` | `on_hand`, `reserved`, `available` (= on_hand − reserved) and location |
| `POST` | `/stores/{store_id}/stock/{sku}/receive` | Record a delivery: `on_hand += qty` |
| `POST` | `/products` | Create a product (409 if the SKU exists) |
| `GET` / `PATCH` / `DELETE` | `/products/{sku}` | Read (Redis-cached, `X-Cache: HIT/MISS`), partially update, soft-delete |
| `GET` | `/products?category=` | List the catalogue (paginated) |
| `POST` | `/orders` | Reserve stock for one or more items, all or nothing (409 if any item is short) |
| `GET` | `/orders/{id}` | Order with line items and total |
| `POST` | `/orders/{id}/confirm` | Sold: units leave the shelf |
| `POST` | `/orders/{id}/cancel` | Released: units become available again |
| `GET` | `/health` | Liveness plus a database round trip |
| `GET` | `:8001/reorder-suggestions?store_id=` | Replenishment: SKUs below `avg daily demand × lead time + safety stock`, most urgent first |

### Examples

Search for "tap" at store 1. Full-text search matches whole words, so "Masking Tape" is not returned:

```bash
curl "http://localhost:8000/stores/1/products?q=tap&limit=2"
```
```json
[
  {"sku": "PLB-00024", "name": "Pillar Tap, Chrome-plated Brass, Long Body", "category": "plumbing",
   "price": "1429.00", "available": 41, "aisle": "14", "bay": "C06"},
  {"sku": "PLB-00025", "name": "Pillar Tap, Chrome-plated Brass, Short Body", "category": "plumbing",
   "price": "1189.00", "available": 71, "aisle": "13", "bay": "A08"}
]
```

Reserve two items in one order:

```bash
curl -X POST http://localhost:8000/orders -H "Content-Type: application/json" \
  -d '{"store_id": 1, "items": [{"sku": "PLB-00024", "qty": 2}, {"sku": "PLB-00037", "qty": 1}]}'
```

If any item is short, nothing is reserved and the response says exactly why:

```json
{"detail": "Out of stock: PLB-00027 at store 1 (requested 50, available 3)",
 "sku": "PLB-00027", "requested": 50, "available": 3}
```

## Design decisions

**Concurrency**
- **Atomic conditional update.** A reservation is one statement: `UPDATE stock SET reserved = reserved + :qty WHERE store_id = :s AND sku = :k AND on_hand - reserved >= :qty`. The row lock makes concurrent updates wait, and Postgres re-checks the `WHERE` clause against the newest row version after the wait. Zero rows updated means 409.
- **`SELECT ... FOR UPDATE` as the alternative.** It is also implemented and tested, and can be selected with `RESERVATION_STRATEGY=for_update`. It suits decisions too complex for one `WHERE` clause, at the cost of an extra round trip and a lock held while application code runs.
- **Deadlock prevention by lock ordering.** Multi-item orders lock stock rows sorted by SKU, so two orders for "X, Y" and "Y, X" queue up instead of deadlocking. A test creates a real deadlock with opposite lock order, then shows sorted order avoids it.
- **All or nothing.** All items of an order are reserved in one transaction. If any item fails, everything rolls back.

**Data model**
- **Shelf location lives on `stock`, not `products`.** Aisle and bay depend on (store, SKU): the same product sits in different aisles in different stores.
- **`available` is computed, never stored**, so there is a single source of truth.
- **CHECK constraints** (`reserved <= on_hand`, no negatives) are a safety net against bugs. They cannot catch lost updates, which is why the atomic update matters.
- **Money is `NUMERIC(10,2)` / `Decimal`, serialised as a JSON string**, never a float.
- **Order lines snapshot `unit_price`**, so later price changes don't rewrite history.
- **Soft delete** for products, because old orders still reference them. UUID order IDs, so IDs in URLs can't be guessed.

**Events and replenishment**
- **Transactional outbox instead of a dual write.** Committing to Postgres and then publishing to Redis is two systems: a crash in between loses the event, and publishing first can announce a change that then rolls back. Each stock change instead inserts its `StockChanged` event into an `outbox` table in the same transaction. A test proves a failed order leaves no events.
- **Relay with at-least-once delivery.** A separate process publishes unpublished outbox rows in id order, then marks them. `FOR UPDATE SKIP LOCKED` lets two relays run without double-publishing, and a partial index keeps the "unpublished" lookup small.
- **Idempotent consumer.** The consumer inserts each `event_id` into `processed_events` in the same transaction as its effect, then acknowledges the message. A redelivered event is skipped, so at-least-once delivery has an effectively-once effect. An older event can't overwrite a newer stock level, and a failing event stays pending for retry without blocking the others.
- **Database per service (schema per service here).** The replenishment service never queries inventory tables. Events carry the post-change stock levels, so it keeps working even when the inventory service is down.
- **Reorder rule:** `available < avg_daily_demand × lead_time_days (3) + safety_stock (5)`, with a 7-day moving average where days without sales count as zero, on the store's local calendar day (Asia/Kolkata), not UTC. The suggested quantity covers the reorder level plus one more window of demand.
- **Mapping to Kafka:** the stream is a topic, a `(store_id, sku)` partition key keeps each SKU's events ordered, a consumer group is a Kafka consumer group, XACK is a committed offset, and stream `MAXLEN` corresponds to topic retention.

**Caching**
- **Cache-aside for product lookups**, with a 5-minute TTL as a safety net. The cache entry is deleted after the database commit, never before, so a concurrent read can't refill it with the old row.
- **The cache is optional.** Redis errors behave like a cache miss. A test found that redis-py's default retries made every request over 12 s slower while Redis was down, so retries are off for the cache and a test now checks that requests stay fast.

**Search**
- **Postgres full-text search with a GIN index.** It replaced `ILIKE '%tap%'`, which returned 14 results for "tap", 7 of them tape. Stemming means "taps" finds taps. `websearch_to_tsquery` supports `pillar tap -steel`. A test checks with `EXPLAIN` that the index is used.

**Engineering**
- **Layered:** routers (HTTP) → services (rules + SQL) → models. Services raise domain errors, and one place maps them to status codes.
- **Hand-written Alembic migrations**, with `alembic check` in CI to prove they match the models.
- **Tests run on real Postgres, never SQLite.** SQLite serialises writers, which would hide the concurrency bugs.
- **Docker:** dependencies are installed before the code is copied (layer caching), the container runs as a non-root user, and `exec uvicorn` gives a graceful shutdown.
- **CI:** migrations, `alembic check`, tests with a 90% coverage gate, then the image is built, all 6 containers start, and a smoke test checks that a reservation reaches the replenishment service through the event pipeline.

## Results

All numbers below were measured on a Windows 11 laptop. The full stack (6 containers) ran in Docker Desktop (WSL 2), with a single uvicorn process per API and the load coming from a client on the same machine.

**Tests:** 96 passing, all against real Postgres and Redis except the unit tests. Coverage is 94.22% of lines and branches. The manual `bench.py` and `demo_events.py` tools are excluded. CI fails below 90%.

**Event pipeline:** after a sale is confirmed, the reorder suggestion appeared in the replenishment API 435 ms later. After a delivery was received, it cleared 461 ms later. That covers API commit → outbox → relay → Redis Stream → consumer → suggestion (`python -m scripts.demo_events`).

**Overselling test:** 50 simultaneous `POST /orders` for the last 10 units of one SKU.

| Strategy | Success (201) | Rejected (409) | Final stock (on hand / reserved / available) |
|---|---|---|---|
| Atomic conditional `UPDATE` | 10 | 40 | 10 / 10 / 0 |
| `SELECT ... FOR UPDATE` | 10 | 40 | 10 / 10 / 0 |
| Naive read-then-write | **50** | 0 | 10 / **5** / 5 (40 units oversold, lost updates) |

**Other concurrency tests:** 20 simultaneous confirms of one order gave exactly 1 success, and stock moved once. 40 racing multi-item orders in opposite SKU order completed with 0 deadlocks. Opposite lock order without sorting was aborted by Postgres with `deadlock detected`.

**Response times:** 1 client, 200 sequential requests per endpoint, after warm-up.

| Endpoint | Median | p95 | p99 |
|---|---|---|---|
| `GET /health` | 10.7 ms | 13.4 ms | 16.2 ms |
| `GET /stores/1/stock/PLB-00024` | 19.1 ms | 26.7 ms | 37.2 ms |
| `GET /stores/1/products?q=tap` | 32.7 ms | 75.6 ms | 110.3 ms |
| `POST /orders` (1 item, writes the outbox event too) | 26.5 ms | 55.8 ms | 94.5 ms |

**Throughput:** 20 concurrent clients, 1,000 search requests, 57 requests/s, median 330 ms, p95 522 ms, 0 errors.

Reproduce these with `python -m scripts.bench` against the running stack.

## Project structure

```
app/
  core/        settings (env vars) and domain errors
  events/      outbox writer and the relay process
  cache.py     Redis cache-aside helpers (fail-open)
  db/          SQLAlchemy base (naming conventions) and sessions
  models/      tables: stores, products, stock, orders, order_items
  schemas/     Pydantic request and response contracts
  services/    business logic: catalogue, stock/search, reservations, orders
  routers/     HTTP endpoints
replenishment/ second service: consumer, moving-average suggestions, API
alembic/       migrations (0001 schema, 0002 full-text index, 0003 outbox)
scripts/       seed.py (deterministic data), bench.py (latency/throughput), demo_events.py
tests/
  unit/        no database needed
  integration/ real Postgres + Redis: API, concurrency, events, replenishment, cache
```

## Possible next steps

Not built yet: a dead-letter stream for events that keep failing; Kafka in place of Redis Streams at higher volume; per-SKU lead times and safety stock instead of global settings; an AI product finder that validates model output against the catalogue; prefix and typo-tolerant search (`pg_trgm`).
