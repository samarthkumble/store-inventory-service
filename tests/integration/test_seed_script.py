"""The seed script end to end, against the test database."""
import sys

from sqlalchemy import func, select

from app.db.session import engine
from app.models import Product, Stock, Store
from scripts import seed


def counts():
    # A short-lived connection, closed straight away. Holding a session open
    # here would keep its read locks, and the seed's TRUNCATE (which needs an
    # exclusive lock) would wait for it forever.
    with engine.connect() as conn:
        return tuple(conn.scalar(select(func.count()).select_from(m))
                     for m in (Store, Product, Stock))


def store_ids():
    with engine.connect() as conn:
        return list(conn.scalars(select(Store.id).order_by(Store.id)))


def test_seed_then_refuse_then_reset(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["seed"])
    assert seed.main() == 0
    assert counts() == (3, 200, 561)

    # A second plain run refuses instead of duplicating data.
    assert seed.main() == 1
    assert "already has products" in capsys.readouterr().out

    # --reset wipes and reseeds: identical data, store ids restart at 1.
    monkeypatch.setattr(sys, "argv", ["seed", "--reset"])
    assert seed.main() == 0
    assert counts() == (3, 200, 561)
    assert store_ids() == [1, 2, 3]
