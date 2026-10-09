"""Watch the event pipeline work, and time it (needs the full Docker stack).

    python -m scripts.demo_events

Sells units through the inventory API, then polls the replenishment API until
the reorder suggestion appears. The time measured covers the whole path:
API commit -> outbox -> relay -> Redis Stream -> consumer -> suggestion.
Then it receives a delivery and times how long the suggestion takes to clear.
"""
import argparse
import time

import httpx2

STORE = 1
SKU = "PLB-00024"   # 41 in stock at store 1 in the seed data


def wait_until(check, timeout_s: float = 15.0) -> float:
    start = time.perf_counter()
    while time.perf_counter() - start < timeout_s:
        if check():
            return (time.perf_counter() - start) * 1000
        time.sleep(0.05)
    raise TimeoutError("the event did not arrive within 15 s - is the relay/consumer running?")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", default="http://127.0.0.1:8000")
    parser.add_argument("--replenishment", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    inv = httpx2.Client(base_url=args.inventory, timeout=10)
    rep = httpx2.Client(base_url=args.replenishment, timeout=10)

    def suggested() -> dict | None:
        rows = rep.get("/reorder-suggestions", params={"store_id": STORE}).json()
        return next((r for r in rows if r["sku"] == SKU), None)

    before = inv.get(f"/stores/{STORE}/stock/{SKU}").json()
    print(f"{SKU} at store {STORE}: available {before['available']}")
    to_sell = before["available"] - 10
    print(f"Selling {to_sell} units in orders of 5 (reserve + confirm each)...")
    while to_sell > 0:
        qty = min(5, to_sell)
        order = inv.post("/orders", json={"store_id": STORE, "items": [{"sku": SKU, "qty": qty}]})
        order.raise_for_status()
        inv.post(f"/orders/{order.json()['id']}/confirm").raise_for_status()
        to_sell -= qty

    lag_ms = wait_until(lambda: suggested() is not None)
    s = suggested()
    print(f"\nReorder suggestion appeared {lag_ms:.0f} ms after the last sale:")
    print(f"  available {s['available']}, avg daily demand {s['avg_daily_demand']}, "
          f"reorder level {s['reorder_level']}, suggested qty {s['suggested_qty']}, "
          f"days of cover {s['days_of_cover']}")

    inv.post(f"/stores/{STORE}/stock/{SKU}/receive", json={"qty": s["suggested_qty"]}).raise_for_status()
    clear_ms = wait_until(lambda: suggested() is None)
    print(f"\nReceived {s['suggested_qty']} units; suggestion cleared {clear_ms:.0f} ms later.")
    print(f"\nEnd-to-end event latency: {lag_ms:.0f} ms (sale -> suggestion), "
          f"{clear_ms:.0f} ms (delivery -> cleared)")


if __name__ == "__main__":
    main()
