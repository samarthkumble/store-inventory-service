"""Reservation strategies called directly (no HTTP), one database session each."""
import pytest
from sqlalchemy import insert

from app.core.exceptions import NotFoundError, OutOfStockError
from app.db.session import engine
from app.models import Store
from app.services.reservation import reserve_atomic, reserve_for_update

STRATEGIES = pytest.mark.parametrize("reserve", [reserve_atomic, reserve_for_update],
                                     ids=["atomic", "for_update"])


@STRATEGIES
def test_reserves_when_enough(reserve, db, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=5)
    reserve(db, store_id, sku, 5)
    db.commit()
    assert stock_of(store_id, sku) == {"on_hand": 5, "reserved": 5, "available": 0}


@STRATEGIES
def test_reports_how_many_are_available(reserve, db, store_id, make_product, stock_of):
    sku = make_product(store_id, "TST-00001", on_hand=5, reserved=3)
    with pytest.raises(OutOfStockError) as err:
        reserve(db, store_id, sku, 3)
    assert err.value.available == 2
    db.rollback()
    assert stock_of(store_id, sku)["reserved"] == 3


@STRATEGIES
def test_sku_not_carried_by_store(reserve, db, store_id, make_product):
    sku = make_product(store_id, "TST-00001", on_hand=5)
    with engine.begin() as conn:
        other = conn.scalar(insert(Store).values(code="TST-02", name="Other", city="Mysuru").returning(Store.id))
    with pytest.raises(NotFoundError, match="not stocked"):
        reserve(db, other, sku, 1)
