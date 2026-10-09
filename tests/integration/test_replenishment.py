"""The replenishment service, end to end and piece by piece."""
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import insert

from app.db.session import SessionLocal
from app.events.relay import relay_once
from replenishment import consumer
from replenishment.config import settings
from replenishment.db import DailyDemand, SessionLocal as ReplenishmentSession, StockLevel
from replenishment.service import reorder_suggestions


def pump(redis_client):
    """Move events: outbox -> Redis Stream -> replenishment consumer."""
    with SessionLocal() as db:
        relay_once(db, redis_client)
    consumer.ensure_group(redis_client)
    return consumer.consume_once(redis_client, block_ms=100)


def sell(client, store_id, sku, qty):
    order_id = client.post("/orders", json={"store_id": store_id,
                                            "items": [{"sku": sku, "qty": qty}]}).json()["id"]
    assert client.post(f"/orders/{order_id}/confirm").status_code == 200


def event(event_type="CONFIRMED", qty=1, available=5, occurred_at=None, event_id=None):
    return {"v": "1", "event_id": event_id or str(uuid.uuid4()), "event_type": event_type,
            "store_id": "1", "sku": "TST-00001", "qty": str(qty), "on_hand": str(available),
            "reserved": "0", "available": str(available), "order_id": "",
            "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat()}


def test_end_to_end_sales_create_a_reorder_suggestion(
        client, replenishment_client, store_id, make_product, redis_client):
    sku = make_product(store_id, "TST-00001", on_hand=30)
    for _ in range(4):
        sell(client, store_id, sku, 7)            # 28 sold today, 2 left

    assert pump(redis_client) == 8                 # 4 RESERVED + 4 CONFIRMED

    body = replenishment_client.get("/reorder-suggestions", params={"store_id": store_id}).json()
    # avg = 28 units / 7 days = 4.0/day; reorder level = 4.0 * 3 + 5 = 17
    assert body == [{"store_id": store_id, "sku": sku, "available": 2,
                     "avg_daily_demand": 4.0, "reorder_level": 17.0,
                     "suggested_qty": 43,          # ceil(17 + 4.0 * 7 - 2)
                     "days_of_cover": 0.5}]

    # A delivery arrives: the suggestion disappears once the event flows through.
    client.post(f"/stores/{store_id}/stock/{sku}/receive", json={"qty": 50})
    assert pump(redis_client) == 1
    assert replenishment_client.get("/reorder-suggestions",
                                    params={"store_id": store_id}).json() == []


def test_duplicate_delivery_is_counted_once():
    e = event(qty=3)
    with ReplenishmentSession() as db, db.begin():
        assert consumer.handle_event(db, e) is True
    with ReplenishmentSession() as db, db.begin():
        assert consumer.handle_event(db, e) is False   # same event_id again
    with ReplenishmentSession() as db:
        today_in_store = datetime.now(consumer.STORE_TZ).date()
        assert db.get(DailyDemand, (1, "TST-00001", today_in_store)).units == 3


def test_older_event_does_not_overwrite_newer_stock_level():
    now = datetime.now(timezone.utc)
    with ReplenishmentSession() as db, db.begin():
        consumer.handle_event(db, event("RECEIVED", available=50, occurred_at=now))
        consumer.handle_event(db, event("RESERVED", available=40,
                                        occurred_at=now - timedelta(minutes=5)))
    with ReplenishmentSession() as db:
        assert db.get(StockLevel, (1, "TST-00001")).available == 50


def test_demand_uses_the_store_local_day():
    # 20:00 UTC on 9 Oct is 01:30 on 10 Oct in Bengaluru (UTC+5:30).
    late_evening_utc = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)
    with ReplenishmentSession() as db, db.begin():
        consumer.handle_event(db, event(qty=2, occurred_at=late_evening_utc))
    with ReplenishmentSession() as db:
        assert db.get(DailyDemand, (1, "TST-00001", date(2026, 10, 10))).units == 2


def test_moving_average_ignores_sales_outside_the_window():
    today = date(2026, 10, 9)
    with ReplenishmentSession() as db, db.begin():
        db.execute(insert(StockLevel).values(store_id=1, sku="TST-00001", on_hand=10,
                                             reserved=0, available=10,
                                             as_of=datetime.now(timezone.utc)))
        db.execute(insert(DailyDemand), [
            {"store_id": 1, "sku": "TST-00001", "day": today - timedelta(days=d), "units": u}
            for d, u in [(0, 7), (3, 7), (6, 7), (7, 700), (30, 700)]])  # last two: too old
        db.flush()
        suggestions = reorder_suggestions(db, store_id=1, today=today)

    # In window: 21 units / 7 days = 3/day -> reorder level 3*3+5 = 14 > 10 available
    assert [(s.avg_daily_demand, s.reorder_level, s.suggested_qty) for s in suggestions] == [
        (3.0, 14.0, 25)]  # ceil(14 + 3*7 - 10)


def test_no_demand_but_below_safety_stock_is_still_suggested():
    with ReplenishmentSession() as db, db.begin():
        db.execute(insert(StockLevel).values(store_id=1, sku="TST-00001", on_hand=2,
                                             reserved=0, available=2,
                                             as_of=datetime.now(timezone.utc)))
        db.flush()
        [s] = reorder_suggestions(db, store_id=1)
    assert (s.reorder_level, s.suggested_qty, s.days_of_cover) == (settings.safety_stock, 3, None)


def test_unacked_event_is_redelivered_and_then_skipped(redis_client):
    """Simulates a crash after the database commit but before XACK."""
    consumer.ensure_group(redis_client)
    redis_client.xadd(settings.stock_events_stream, event(qty=4))
    # Deliver it but "crash" before acking: read it without processing.
    redis_client.xreadgroup(settings.consumer_group, settings.consumer_name,
                            {settings.stock_events_stream: ">"}, count=10)
    assert redis_client.xpending(settings.stock_events_stream,
                                 settings.consumer_group)["pending"] == 1

    assert consumer.consume_once(redis_client, block_ms=100) == 1   # picked up from pending
    assert redis_client.xpending(settings.stock_events_stream,
                                 settings.consumer_group)["pending"] == 0


def test_health(replenishment_client):
    assert replenishment_client.get("/health").json() == {
        "status": "ok", "database": "ok", "redis": "ok"}


def test_store_id_is_required(replenishment_client):
    assert replenishment_client.get("/reorder-suggestions").status_code == 422


def test_bad_event_stays_pending_and_does_not_block_others(redis_client):
    """A malformed event fails, stays un-acked for retry, and the next event still flows."""
    consumer.ensure_group(redis_client)
    redis_client.xadd(settings.stock_events_stream, {**event(), "event_id": "not-a-uuid"})
    redis_client.xadd(settings.stock_events_stream, event(qty=2))

    assert consumer.consume_once(redis_client, block_ms=100) == 1   # only the good one
    assert redis_client.xpending(settings.stock_events_stream,
                                 settings.consumer_group)["pending"] == 1


def test_ensure_group_is_safe_to_call_twice(redis_client):
    consumer.ensure_group(redis_client)
    consumer.ensure_group(redis_client)   # BUSYGROUP is swallowed
