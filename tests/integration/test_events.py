"""StockChanged events: written atomically with stock changes, relayed to Redis."""
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal, engine
from app.events.relay import relay_once
from app.models import OutboxEvent


def outbox_payloads():
    with engine.connect() as conn:
        return [row.payload for row in conn.execute(
            select(OutboxEvent.payload).order_by(OutboxEvent.id))]


def order(client, store_id, *items):
    return client.post("/orders", json={"store_id": store_id,
                                        "items": [{"sku": s, "qty": q} for s, q in items]})


def test_each_stock_change_records_one_event_with_new_levels(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = order(client, store_id, (sku, 3)).json()["id"]
    client.post(f"/orders/{order_id}/confirm")
    client.post(f"/stores/{store_id}/stock/{sku}/receive", json={"qty": 20})

    events = outbox_payloads()

    assert [e["event_type"] for e in events] == ["RESERVED", "CONFIRMED", "RECEIVED"]
    assert [(e["on_hand"], e["reserved"], e["available"]) for e in events] == [
        (10, 3, 7), (7, 0, 7), (27, 0, 27)]
    assert events[0]["order_id"] == events[1]["order_id"] == order_id
    assert len({e["event_id"] for e in events}) == 3  # unique ids for dedup
    assert all(e["v"] == 1 and e["store_id"] == store_id and e["sku"] == sku for e in events)


def test_cancel_records_cancelled_event(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = order(client, store_id, (sku, 4)).json()["id"]
    client.post(f"/orders/{order_id}/cancel")
    assert [(e["event_type"], e["available"]) for e in outbox_payloads()] == [
        ("RESERVED", 6), ("CANCELLED", 10)]


def test_failed_order_records_no_events(client, store_id, make_product):
    """The point of the outbox: the first item's RESERVED event rolls back
    together with its reservation when the second item is out of stock."""
    plenty = make_product(store_id, "TST-00001", on_hand=10)
    scarce = make_product(store_id, "TST-00002", on_hand=1)

    assert order(client, store_id, (plenty, 2), (scarce, 5)).status_code == 409
    assert outbox_payloads() == []


def test_receive_validates_input(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    assert client.post(f"/stores/{store_id}/stock/{sku}/receive", json={"qty": 0}).status_code == 422
    assert client.post(f"/stores/{store_id}/stock/TST-09999/receive", json={"qty": 1}).status_code == 404
    assert client.post(f"/stores/999/stock/{sku}/receive", json={"qty": 1}).status_code == 404
    r = client.post(f"/stores/{store_id}/stock/{sku}/receive", json={"qty": 5})
    assert (r.json()["on_hand"], r.json()["available"]) == (15, 15)


def test_relay_publishes_in_order_and_only_once(client, store_id, make_product, redis_client):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    for _ in range(3):
        order(client, store_id, (sku, 1))

    with SessionLocal() as db:
        assert relay_once(db, redis_client) == 3
    with SessionLocal() as db:
        assert relay_once(db, redis_client) == 0  # already marked published

    entries = redis_client.xrange(settings.stock_events_stream)
    assert [fields["available"] for _, fields in entries] == ["9", "8", "7"]
    assert all(fields["event_type"] == "RESERVED" for _, fields in entries)
    with engine.connect() as conn:
        assert conn.scalar(select(OutboxEvent.id).where(OutboxEvent.published_at.is_(None))) is None


def test_relay_batches(client, store_id, make_product, redis_client):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    for _ in range(5):
        order(client, store_id, (sku, 1))
    with SessionLocal() as db:
        assert relay_once(db, redis_client, batch_size=2) == 2
    with SessionLocal() as db:
        assert relay_once(db, redis_client, batch_size=10) == 3
