"""The core of Milestone 2: no overselling under real concurrency.

Every test fires genuinely simultaneous HTTP requests at a real uvicorn
server (the live_server fixture), which talks to a real Postgres. Each
request runs in its own server thread with its own database connection,
exactly like production traffic.

Run with -s to see the printed numbers:  pytest tests/integration/test_concurrency.py -s
"""
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx2
import pytest
from sqlalchemy import func, select, text

from app.db.session import engine
from app.models import Order
from app.services.reservation import get_reserve_strategy, reserve_atomic, reserve_for_update
from app.main import app
from tests.integration.naive_reservation import reserve_naive

REQUESTS = 50
UNITS_LEFT = 10


def fire_simultaneously(url: str, bodies: list[dict]) -> tuple[Counter, list[float]]:
    """POST every body at the same instant; return status-code counts and latencies (ms)."""
    gate = threading.Barrier(len(bodies))

    def one(body):
        with httpx2.Client(timeout=60) as http:
            gate.wait()  # every thread waits here, then all are released together
            start = time.perf_counter()
            status = http.post(url, json=body).status_code
            return status, (time.perf_counter() - start) * 1000

    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:
        results = list(pool.map(one, bodies))
    return Counter(s for s, _ in results), [ms for _, ms in results]


def report(label, statuses, latencies, stock):
    lat = sorted(latencies)
    p95 = lat[int(0.95 * (len(lat) - 1))]
    print(f"\n[{label}] {REQUESTS} requests for the last {UNITS_LEFT} units -> "
          f"201: {statuses[201]}, 409: {statuses[409]}, other: "
          f"{sum(v for k, v in statuses.items() if k not in (201, 409))} | "
          f"stock after: {stock} | latency median {statistics.median(lat):.0f} ms, "
          f"p95 {p95:.0f} ms")


def count_orders() -> int:
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(Order))


@pytest.mark.parametrize("strategy", [reserve_atomic, reserve_for_update],
                         ids=["atomic_update", "select_for_update"])
def test_50_requests_for_last_10_units(strategy, live_server, store_id, make_product, stock_of):
    app.dependency_overrides[get_reserve_strategy] = lambda: strategy
    sku = make_product(store_id, "TST-00001", on_hand=UNITS_LEFT)
    body = {"store_id": store_id, "items": [{"sku": sku, "qty": 1}]}

    statuses, latencies = fire_simultaneously(f"{live_server}/orders", [body] * REQUESTS)
    stock = stock_of(store_id, sku)
    report(strategy.__name__, statuses, latencies, stock)

    assert statuses == Counter({201: UNITS_LEFT, 409: REQUESTS - UNITS_LEFT})
    assert stock == {"on_hand": UNITS_LEFT, "reserved": UNITS_LEFT, "available": 0}
    assert count_orders() == UNITS_LEFT  # one order row per success, none for 409s


def test_naive_read_then_write_oversells(live_server, store_id, make_product, stock_of):
    """Documents the bug: this test PASSES when the naive code oversells.

    Same 50 requests, same 10 units, but using the read-check-write version.
    """
    app.dependency_overrides[get_reserve_strategy] = lambda: reserve_naive
    sku = make_product(store_id, "TST-00001", on_hand=UNITS_LEFT)
    body = {"store_id": store_id, "items": [{"sku": sku, "qty": 1}]}

    statuses, latencies = fire_simultaneously(f"{live_server}/orders", [body] * REQUESTS)
    stock = stock_of(store_id, sku)
    report("reserve_naive", statuses, latencies, stock)
    sold = statuses[201]
    print(f"[reserve_naive] customers told 'reserved': {sold}, units actually recorded "
          f"as reserved: {stock['reserved']}, order rows: {count_orders()}")

    assert sold > UNITS_LEFT, "expected the naive version to oversell"
    # The silent part: the database says everything is fine.
    assert stock["reserved"] <= stock["on_hand"]
    assert sold > stock["reserved"]  # lost updates: promises the stock row never recorded


def test_double_confirm_race_moves_stock_once(live_server, client, store_id, make_product, stock_of):
    """20 simultaneous 'confirm' clicks on one order: exactly one may succeed."""
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json={"store_id": store_id,
                                            "items": [{"sku": sku, "qty": 4}]}).json()["id"]

    statuses, _ = fire_simultaneously(f"{live_server}/orders/{order_id}/confirm", [None] * 20)

    assert statuses == Counter({200: 1, 409: 19})
    assert stock_of(store_id, sku) == {"on_hand": 6, "reserved": 0, "available": 6}


def _lock_two_rows(store_id, first, second, gate, errors):
    """One transaction that updates two stock rows, pausing in between."""
    try:
        with engine.begin() as conn:
            conn.execute(text("UPDATE stock SET reserved = reserved + 1 "
                              "WHERE store_id = :s AND sku = :k"), {"s": store_id, "k": first})
            if gate is not None:
                gate.wait(timeout=10)  # make sure both hold their first lock
            conn.execute(text("UPDATE stock SET reserved = reserved + 1 "
                              "WHERE store_id = :s AND sku = :k"), {"s": store_id, "k": second})
    except Exception as exc:  # noqa: BLE001
        errors.append(exc)


def _run_pair(store_id, order_a, order_b, gate):
    errors: list[Exception] = []
    threads = [threading.Thread(target=_lock_two_rows, args=(store_id, *order_a, gate, errors)),
               threading.Thread(target=_lock_two_rows, args=(store_id, *order_b, gate, errors))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return errors


def test_opposite_lock_order_deadlocks(store_id, make_product):
    """A locks X then wants Y; B locks Y then wants X. Each waits for the other forever."""
    x = make_product(store_id, "TST-00001", on_hand=10)
    y = make_product(store_id, "TST-00002", on_hand=10)

    errors = _run_pair(store_id, (x, y), (y, x), threading.Barrier(2))

    # Postgres notices the cycle (after deadlock_timeout, 1 s by default),
    # kills ONE transaction with "deadlock detected" and lets the other finish.
    assert len(errors) == 1
    assert "deadlock detected" in str(errors[0])
    print(f"\n[deadlock] Postgres aborted one transaction: {str(errors[0]).splitlines()[0]}")


def test_same_lock_order_never_deadlocks(store_id, make_product, stock_of):
    """The fix: both lock X first. B simply waits for A, then proceeds."""
    x = make_product(store_id, "TST-00001", on_hand=10)
    y = make_product(store_id, "TST-00002", on_hand=10)

    # No barrier: B blocks on X while A holds it, so a barrier would never fill.
    errors = _run_pair(store_id, (x, y), (x, y), None)

    assert errors == []
    assert stock_of(store_id, x)["reserved"] == 2
    assert stock_of(store_id, y)["reserved"] == 2


def test_concurrent_multi_item_orders_in_opposite_order(live_server, store_id, make_product, stock_of):
    """End to end: the API sorts SKUs, so "X,Y" and "Y,X" orders racing never deadlock."""
    x = make_product(store_id, "TST-00001", on_hand=100)
    y = make_product(store_id, "TST-00002", on_hand=100)
    xy = {"store_id": store_id, "items": [{"sku": x, "qty": 1}, {"sku": y, "qty": 1}]}
    yx = {"store_id": store_id, "items": [{"sku": y, "qty": 1}, {"sku": x, "qty": 1}]}

    statuses, _ = fire_simultaneously(f"{live_server}/orders", [xy, yx] * 20)

    assert statuses == Counter({201: 40})
    assert stock_of(store_id, x)["reserved"] == 40
    assert stock_of(store_id, y)["reserved"] == 40
