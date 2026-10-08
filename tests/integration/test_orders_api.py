"""Order lifecycle through the HTTP API, against real Postgres."""
import uuid

from sqlalchemy import func, insert, select

from app.db.session import engine
from app.models import Order, Store


def order_body(store_id, *items):
    return {"store_id": store_id, "items": [{"sku": s, "qty": q} for s, q in items]}


def test_reserve_increases_reserved_not_on_hand(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10, price="250.00")

    r = client.post("/orders", json=order_body(store_id, (sku, 3)))

    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "RESERVED"
    assert body["items"] == [{"sku": sku, "qty": 3, "unit_price": "250.00"}]
    assert body["total"] == "750.00"
    assert stock_of(store_id, sku) == {"on_hand": 10, "reserved": 3, "available": 7}


def test_duplicate_lines_are_merged(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10)

    r = client.post("/orders", json=order_body(store_id, (sku, 2), (sku, 3)))

    assert r.status_code == 201
    assert r.json()["items"][0]["qty"] == 5
    assert stock_of(store_id, sku)["reserved"] == 5


def test_reserving_exactly_all_available_succeeds(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10, reserved=4)

    r = client.post("/orders", json=order_body(store_id, (sku, 6)))

    assert r.status_code == 201
    assert stock_of(store_id, sku)["available"] == 0


def test_out_of_stock_returns_409_with_details(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10, reserved=8)

    r = client.post("/orders", json=order_body(store_id, (sku, 3)))

    assert r.status_code == 409
    assert r.json()["sku"] == sku
    assert r.json()["requested"] == 3
    assert r.json()["available"] == 2
    assert stock_of(store_id, sku)["reserved"] == 8  # unchanged


def test_multi_item_order_is_all_or_nothing(client, store_id, make_product, stock_of, db):
    """The first item fits, the second doesn't: the first must be rolled back too."""
    plenty = make_product(store_id, "TST-00001", on_hand=10)
    scarce = make_product(store_id, "TST-00002", on_hand=1)

    r = client.post("/orders", json=order_body(store_id, (plenty, 2), (scarce, 5)))

    assert r.status_code == 409
    assert stock_of(store_id, plenty)["reserved"] == 0
    assert stock_of(store_id, scarce)["reserved"] == 0
    assert db.scalar(select(func.count()).select_from(Order)) == 0


def test_unknown_sku_returns_404(client, store_id):
    r = client.post("/orders", json=order_body(store_id, ("TST-09999", 1)))
    assert r.status_code == 404


def test_sku_not_stocked_at_store_returns_404(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    with engine.begin() as conn:
        other = conn.scalar(insert(Store).values(code="TST-02", name="Other", city="Mysuru").returning(Store.id))

    r = client.post("/orders", json=order_body(other, (sku, 1)))

    assert r.status_code == 404
    assert "not stocked" in r.json()["detail"]


def test_unknown_store_returns_404(client):
    r = client.post("/orders", json=order_body(999, ("TST-00001", 1)))
    assert r.status_code == 404


def test_invalid_body_returns_422(client, store_id):
    assert client.post("/orders", json={"store_id": store_id, "items": []}).status_code == 422
    assert client.post("/orders", json=order_body(store_id, ("TST-00001", 0))).status_code == 422
    assert client.post("/orders", json=order_body(store_id, ("bad-sku", 1))).status_code == 422


def test_confirm_removes_units_from_shelf(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json=order_body(store_id, (sku, 4))).json()["id"]

    r = client.post(f"/orders/{order_id}/confirm")

    assert r.status_code == 200
    assert r.json()["status"] == "CONFIRMED"
    assert stock_of(store_id, sku) == {"on_hand": 6, "reserved": 0, "available": 6}


def test_cancel_releases_reservation(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json=order_body(store_id, (sku, 4))).json()["id"]

    r = client.post(f"/orders/{order_id}/cancel")

    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    assert stock_of(store_id, sku) == {"on_hand": 10, "reserved": 0, "available": 10}


def test_confirm_twice_is_rejected_and_stock_moves_once(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json=order_body(store_id, (sku, 4))).json()["id"]
    client.post(f"/orders/{order_id}/confirm")

    r = client.post(f"/orders/{order_id}/confirm")

    assert r.status_code == 409
    assert stock_of(store_id, sku)["on_hand"] == 6


def test_cannot_cancel_a_confirmed_order(client, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json=order_body(store_id, (sku, 4))).json()["id"]
    client.post(f"/orders/{order_id}/confirm")

    r = client.post(f"/orders/{order_id}/cancel")

    assert r.status_code == 409
    assert stock_of(store_id, sku) == {"on_hand": 6, "reserved": 0, "available": 6}


def test_get_order_and_unknown_order(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10)
    order_id = client.post("/orders", json=order_body(store_id, (sku, 1))).json()["id"]

    assert client.get(f"/orders/{order_id}").json()["id"] == order_id
    assert client.get(f"/orders/{uuid.uuid4()}").status_code == 404
    assert client.post(f"/orders/{uuid.uuid4()}/confirm").status_code == 404


def test_price_snapshot_survives_price_change(client, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=10, price="899.00")
    order_id = client.post("/orders", json=order_body(store_id, (sku, 1))).json()["id"]

    client.patch(f"/products/{sku}", json={"price": "949.00"})

    assert client.get(f"/orders/{order_id}").json()["items"][0]["unit_price"] == "899.00"
