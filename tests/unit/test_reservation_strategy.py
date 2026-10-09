"""Configuration picks the reservation strategy; errors carry useful details."""
import pytest

from app.core import config
from app.core.exceptions import ConflictError, OutOfStockError
from app.services.reservation import get_reserve_strategy, reserve_atomic, reserve_for_update


@pytest.mark.parametrize("name, expected", [("atomic", reserve_atomic),
                                            ("for_update", reserve_for_update)])
def test_strategy_comes_from_settings(monkeypatch, name, expected):
    monkeypatch.setattr(config.settings, "reservation_strategy", name)
    assert get_reserve_strategy() is expected


def test_out_of_stock_error_is_a_conflict_with_details():
    err = OutOfStockError(store_id=1, sku="PLB-00024", requested=5, available=2)
    assert isinstance(err, ConflictError)  # so it maps to HTTP 409
    assert (err.sku, err.requested, err.available) == ("PLB-00024", 5, 2)
    assert "requested 5, available 2" in str(err)
