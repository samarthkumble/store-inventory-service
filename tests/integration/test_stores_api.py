"""Store-scoped reads: stock levels and full-text product search."""
from sqlalchemy import insert

from app.db.session import engine
from app.models import Store


def names(response):
    return [p["name"] for p in response.json()]


def seed_search_products(store_id, make_product):
    make_product(store_id, "PLB-00001", 10, name="Pillar Tap, Chrome", category="plumbing", aisle="14", bay="C06")
    make_product(store_id, "PLB-00002", 10, name="Wall Mixer Tap, Matte Black", category="plumbing")
    make_product(store_id, "PLB-00003", 10, name="Tap Washer Kit, Rubber", category="plumbing")
    make_product(store_id, "ELC-00001", 10, name="PVC Insulation Tape, Black", category="electrical")
    make_product(store_id, "PNT-00001", 10, name="Masking Tape, 48 mm", category="paint")


def test_list_stores(client, store_id):
    assert client.get("/stores").json() == [
        {"id": store_id, "code": "TST-01", "name": "Test Store", "city": "Bengaluru"}]


def test_stock_shows_available_and_location(client, store_id, make_product):
    make_product(store_id, "PLB-00001", on_hand=10, reserved=3, aisle="14", bay="C06")

    body = client.get(f"/stores/{store_id}/stock/PLB-00001").json()

    assert (body["on_hand"], body["reserved"], body["available"]) == (10, 3, 7)
    assert (body["aisle"], body["bay"]) == ("14", "C06")


def test_stock_404s(client, store_id):
    assert client.get("/stores/999/stock/PLB-00001").status_code == 404
    r = client.get(f"/stores/{store_id}/stock/PLB-00001")
    assert r.status_code == 404
    assert "not stocked" in r.json()["detail"]


def test_search_tap_does_not_match_tape(client, store_id, make_product):
    """The bug ILIKE '%tap%' had: substring matching returned tapes for 'tap'."""
    seed_search_products(store_id, make_product)

    found = names(client.get(f"/stores/{store_id}/products", params={"q": "tap"}))

    assert sorted(found) == ["Pillar Tap, Chrome", "Tap Washer Kit, Rubber",
                             "Wall Mixer Tap, Matte Black"]


def test_search_matches_word_stems(client, store_id, make_product):
    seed_search_products(store_id, make_product)
    assert len(names(client.get(f"/stores/{store_id}/products", params={"q": "taps"}))) == 3


def test_search_supports_exclusion_and_category(client, store_id, make_product):
    seed_search_products(store_id, make_product)
    url = f"/stores/{store_id}/products"

    assert names(client.get(url, params={"q": "tap -mixer -washer"})) == ["Pillar Tap, Chrome"]
    assert names(client.get(url, params={"q": "tape", "category": "paint"})) == ["Masking Tape, 48 mm"]


def test_search_result_has_location_and_availability(client, store_id, make_product):
    seed_search_products(store_id, make_product)
    hit = client.get(f"/stores/{store_id}/products", params={"q": "pillar"}).json()[0]
    assert (hit["sku"], hit["available"], hit["aisle"], hit["bay"]) == ("PLB-00001", 10, "14", "C06")


def test_search_only_shows_products_carried_by_that_store(client, store_id, make_product):
    seed_search_products(store_id, make_product)
    with engine.begin() as conn:
        other = conn.scalar(insert(Store).values(code="TST-02", name="Other", city="Mysuru").returning(Store.id))
    assert client.get(f"/stores/{other}/products", params={"q": "tap"}).json() == []


def test_search_without_query_lists_by_sku_and_paginates(client, store_id, make_product):
    seed_search_products(store_id, make_product)
    r = client.get(f"/stores/{store_id}/products", params={"limit": 2})
    assert [p["sku"] for p in r.json()] == ["ELC-00001", "PLB-00001"]


def test_search_unknown_store_404(client):
    assert client.get("/stores/999/products", params={"q": "tap"}).status_code == 404


def test_search_uses_the_gin_index(db):
    """EXPLAIN proves the query can use ix_products_name_fts (seq scans disabled
    because with a handful of rows Postgres would rightly prefer one)."""
    from sqlalchemy import func, select, text
    from sqlalchemy.dialects import postgresql
    from app.models import Product
    from app.models.product import ENGLISH, PRODUCT_NAME_TSVECTOR

    stmt = select(Product.sku).where(
        PRODUCT_NAME_TSVECTOR.op("@@")(func.websearch_to_tsquery(ENGLISH, "tap")))
    sql = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    db.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(db.execute(text(f"EXPLAIN {sql}")).scalars())
    assert "ix_products_name_fts" in plan
