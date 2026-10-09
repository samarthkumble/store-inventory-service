"""Request validation rules. Pure Python: no database, no web server."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.order import OrderCreate, OrderOut
from app.schemas.product import ProductCreate, ProductUpdate

VALID = {"sku": "PLB-00024", "name": "Pillar Tap", "category": "plumbing", "price": "1429.00"}


def test_valid_product_parses_price_as_exact_decimal():
    product = ProductCreate(**VALID)
    assert product.price == Decimal("1429.00")
    assert isinstance(product.price, Decimal)


@pytest.mark.parametrize("field, bad_value", [
    ("sku", "plb-00024"),       # lowercase
    ("sku", "PLB-0024"),        # 4 digits
    ("name", ""),
    ("category", "paints"),     # not in the fixed list
    ("price", "-1"),
    ("price", "10.999"),        # more than 2 decimal places
    ("price", "123456789.00"),  # more than 10 digits in total
])
def test_invalid_product_fields_are_rejected(field, bad_value):
    with pytest.raises(ValidationError) as err:
        ProductCreate(**{**VALID, field: bad_value})
    assert err.value.errors()[0]["loc"] == (field,)


def test_unknown_fields_are_rejected_not_ignored():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProductCreate(**VALID, prise="99")


def test_update_can_omit_fields_but_not_null_them():
    assert ProductUpdate(price="10.00").model_dump(exclude_unset=True) == {"price": Decimal("10.00")}
    with pytest.raises(ValidationError, match="not set to null"):
        ProductUpdate(price=None)


def test_update_cannot_change_sku():
    with pytest.raises(ValidationError):
        ProductUpdate(sku="PLB-00099")


@pytest.mark.parametrize("items", [
    [],                                          # nothing to order
    [{"sku": "PLB-00024", "qty": 0}],            # zero quantity
    [{"sku": "PLB-00024", "qty": 1001}],         # above the per-line cap
    [{"sku": "PLB-00024", "qty": 1}] * 51,       # too many lines
])
def test_invalid_orders_are_rejected(items):
    with pytest.raises(ValidationError):
        OrderCreate(store_id=1, items=items)


def test_order_total_uses_line_price_snapshots():
    now = datetime.now(timezone.utc)
    order = OrderOut(
        id=uuid4(), store_id=1, status="RESERVED", created_at=now, updated_at=now,
        items=[{"sku": "PLB-00024", "qty": 3, "unit_price": Decimal("1429.00")},
               {"sku": "PLB-00037", "qty": 1, "unit_price": Decimal("99.00")}],
    )
    assert order.total == Decimal("4386.00")
    assert order.model_dump(mode="json")["total"] == "4386.00"  # money as a string in JSON
