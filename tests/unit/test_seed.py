"""The seed data generator is deterministic and realistic."""
import random
from collections import Counter
from decimal import Decimal

import pytest

from scripts.seed import CATALOG, PRODUCTS_PER_CATEGORY, build_products, build_stock, retail_price


@pytest.mark.parametrize("raw, expected", [
    (1243.7, "1239.00"), (1247.0, "1249.00"), (320.0, "319.00"), (1.0, "9.00"), (99.4, "99.00")])
def test_retail_price_ends_in_9(raw, expected):
    assert retail_price(raw) == Decimal(expected)


def test_products_are_deterministic_for_a_fixed_seed():
    assert build_products(random.Random(42)) == build_products(random.Random(42))


def test_200_products_40_per_category_with_unique_skus():
    products = build_products(random.Random(42))
    assert len(products) == 200
    assert Counter(p["category"] for p in products) == {c: PRODUCTS_PER_CATEGORY for c in CATALOG}
    assert len({p["sku"] for p in products}) == 200


def test_stock_rows_are_valid_and_each_store_has_its_own_layout():
    rng = random.Random(42)
    products = build_products(rng)
    rows = build_stock(rng, [1, 2, 3], products)

    assert all(r["on_hand"] >= 0 and r["reserved"] == 0 for r in rows)
    assert all(len(r["aisle"]) <= 5 and len(r["bay"]) <= 5 for r in rows)

    category = {p["sku"]: p["category"] for p in products}
    aisles = {}  # (store, category) -> set of aisles used
    for r in rows:
        aisles.setdefault((r["store_id"], category[r["sku"]]), set()).add(int(r["aisle"]))
    # Within a store, each category occupies its own block of 5 aisles...
    for store in (1, 2, 3):
        blocks = [min(aisles[(store, c)]) // 5 for c in CATALOG if (store, c) in aisles]
        assert len(set(blocks)) == len(blocks)
    # ...and at least one category sits in a different place in different stores.
    assert any(aisles[(1, c)] != aisles[(2, c)] for c in CATALOG)
