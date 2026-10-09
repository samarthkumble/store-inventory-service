"""Cache-aside for GET /products/{sku}."""
import time

from app import cache
from app.core.config import settings

NEW = {"sku": "PLB-00500", "name": "Pillar Tap, Gunmetal", "category": "plumbing", "price": "1899.00"}


def test_second_read_is_a_cache_hit(client):
    client.post("/products", json=NEW)
    first = client.get("/products/PLB-00500")
    second = client.get("/products/PLB-00500")
    assert first.headers["X-Cache"] == "MISS"
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_update_invalidates_the_cache(client):
    client.post("/products", json=NEW)
    client.get("/products/PLB-00500")                       # now cached
    client.patch("/products/PLB-00500", json={"price": "1949.00"})

    r = client.get("/products/PLB-00500")
    assert r.headers["X-Cache"] == "MISS"
    assert r.json()["price"] == "1949.00"                   # never the stale price


def test_delete_invalidates_the_cache(client):
    client.post("/products", json=NEW)
    client.get("/products/PLB-00500")
    client.delete("/products/PLB-00500")
    assert client.get("/products/PLB-00500").status_code == 404


def test_entries_expire(client, redis_client):
    client.post("/products", json=NEW)
    client.get("/products/PLB-00500")
    assert 0 < redis_client.ttl("product:PLB-00500") <= 300


def test_api_still_works_and_stays_fast_when_redis_is_down(client, monkeypatch):
    client.post("/products", json=NEW)
    # Point the app's own client at a port where nothing listens.
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(cache, "_client", None)

    start = time.perf_counter()
    r = client.get("/products/PLB-00500")                   # get + set both fail
    patch = client.patch("/products/PLB-00500", json={"price": "1.00"})  # delete fails
    elapsed = time.perf_counter() - start

    assert r.status_code == 200 and r.headers["X-Cache"] == "MISS"
    assert patch.status_code == 200
    # Without retry=0 in app/cache.py this took over 12 s (redis-py's default retries).
    assert elapsed < 2
