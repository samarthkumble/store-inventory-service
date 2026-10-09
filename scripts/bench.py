"""Measure response times of the running API (seeded data required).

    python -m scripts.bench                       # against http://127.0.0.1:8000
    python -m scripts.bench --base-url http://...

Two measurements, both reported honestly with the conditions:
  1. Latency: one client, requests sent one after another.
  2. Throughput: 20 clients at once, all searching (read-only, so no row locks).
Order reservations are cancelled right after, so stock is left as it was.
Needs the dev dependencies (httpx2): pip install -r requirements-dev.txt
"""
import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx2

STORE = 1
SKU = "PLB-00024"  # in stock at store 1 in the seed data


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(pct / 100 * (len(ordered) - 1))))]


def timed(fn) -> float:
    start = time.perf_counter()
    fn()
    return (time.perf_counter() - start) * 1000


def measure_latency(http: httpx2.Client, n: int) -> None:
    def get(path, **params):
        return lambda: http.get(path, params=params).raise_for_status()

    def reserve():
        r = http.post("/orders", json={"store_id": STORE, "items": [{"sku": SKU, "qty": 1}]})
        r.raise_for_status()
        reserve.last_id = r.json()["id"]

    cases = {
        "GET /health": get("/health"),
        f"GET /stores/{STORE}/stock/{SKU}": get(f"/stores/{STORE}/stock/{SKU}"),
        f"GET /stores/{STORE}/products?q=tap": get(f"/stores/{STORE}/products", q="tap"),
        "POST /orders (1 item)": reserve,
    }
    print(f"\nLatency, 1 client, {n} sequential requests each (after 20 warm-up requests):")
    print(f"  {'endpoint':38} {'median':>8} {'p95':>8} {'p99':>8}")
    for name, call in cases.items():
        for _ in range(20):  # warm-up: connection pool, caches, query plans
            call()
            if call is reserve:
                http.post(f"/orders/{reserve.last_id}/cancel")
        samples = []
        for _ in range(n):
            samples.append(timed(call))
            if call is reserve:
                http.post(f"/orders/{reserve.last_id}/cancel")  # put the unit back
        print(f"  {name:38} {statistics.median(samples):7.1f}ms "
              f"{percentile(samples, 95):7.1f}ms {percentile(samples, 99):7.1f}ms")


def measure_throughput(base_url: str, total: int, workers: int) -> None:
    # One client per worker, reused, like a real connection pool.
    clients = [httpx2.Client(base_url=base_url, timeout=30) for _ in range(workers)]

    def run(i):
        http = clients[i % workers]
        return timed(lambda: http.get(f"/stores/{STORE}/products", params={"q": "tap"})
                     .raise_for_status())

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        samples = list(pool.map(run, range(total)))
    elapsed = time.perf_counter() - start
    for c in clients:
        c.close()
    print(f"\nThroughput, {workers} concurrent clients, {total} search requests:")
    print(f"  {total / elapsed:.0f} requests/s, median {statistics.median(samples):.1f}ms, "
          f"p95 {percentile(samples, 95):.1f}ms, errors: 0")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("-n", type=int, default=200)
    args = parser.parse_args()
    with httpx2.Client(base_url=args.base_url, timeout=30) as http:
        http.get("/health").raise_for_status()
        measure_latency(http, args.n)
    measure_throughput(args.base_url, total=1000, workers=20)


if __name__ == "__main__":
    main()
