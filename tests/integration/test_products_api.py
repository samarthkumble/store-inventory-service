"""Product catalogue CRUD through the API."""

NEW = {"sku": "PLB-00500", "name": "Pillar Tap, Gunmetal", "category": "plumbing", "price": "1899.00"}


def test_create_then_get(client):
    r = client.post("/products", json=NEW)
    assert r.status_code == 201
    assert r.json()["price"] == "1899.00"
    assert client.get("/products/PLB-00500").json()["name"] == "Pillar Tap, Gunmetal"


def test_duplicate_sku_returns_409(client):
    client.post("/products", json=NEW)
    r = client.post("/products", json={**NEW, "name": "Another"})
    assert r.status_code == 409


def test_patch_changes_only_sent_fields(client):
    client.post("/products", json=NEW)
    r = client.patch("/products/PLB-00500", json={"price": "1949.00"})
    assert r.status_code == 200
    assert r.json()["price"] == "1949.00"
    assert r.json()["name"] == NEW["name"]
    assert r.json()["updated_at"] >= r.json()["created_at"]


def test_soft_delete_hides_product_but_keeps_sku_taken(client):
    client.post("/products", json=NEW)

    assert client.delete("/products/PLB-00500").status_code == 204
    assert client.get("/products/PLB-00500").status_code == 404
    assert client.delete("/products/PLB-00500").status_code == 404
    # Old orders may still reference it, so the SKU can't be reused.
    assert client.post("/products", json=NEW).status_code == 409


def test_list_filters_by_category_and_paginates(client):
    for i, cat in enumerate(["paint", "paint", "paint", "garden"], start=1):
        client.post("/products", json={**NEW, "sku": f"TST-0000{i}", "category": cat})

    page1 = client.get("/products", params={"category": "paint", "limit": 2}).json()
    page2 = client.get("/products", params={"category": "paint", "limit": 2, "offset": 2}).json()

    assert [p["sku"] for p in page1] == ["TST-00001", "TST-00002"]
    assert [p["sku"] for p in page2] == ["TST-00003"]
    assert len(client.get("/products").json()) == 4  # no filter: every category


def test_unknown_product_and_bad_sku_format(client):
    assert client.get("/products/PLB-09999").status_code == 404
    assert client.get("/products/not-a-sku").status_code == 422
    assert client.patch("/products/PLB-09999", json={"price": "1.00"}).status_code == 404


def test_health_checks_the_database(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
