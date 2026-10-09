"""Fixtures for integration tests: a real Postgres, real migrations.

Tests run against a REAL Postgres, in a separate database (inventory_test).
Never SQLite: SQLite effectively runs one writer at a time, so concurrency
bugs would simply disappear in tests.

Unit tests (tests/unit) don't use any of this, so they run without a database.
"""
import socket
import threading
import time
from decimal import Decimal

import pytest
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, make_url, select, text

from app.db.session import SessionLocal, engine
from app.main import app
from app.models import Product, Stock, Store
from tests.conftest import TEST_DATABASE_URL

TABLES = "order_items, orders, stock, products, stores"


def _create_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    # CREATE DATABASE can't run inside a transaction, hence AUTOCOMMIT,
    # connected to the built-in "postgres" maintenance database.
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        )
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    """Once per test run: create the test DB and apply the real migrations.

    Using the migrations (not Base.metadata.create_all) means every test run
    also proves the migrations themselves work.
    """
    _create_test_database()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db(test_database):
    """Before every test: empty tables, so tests can't affect each other."""
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY"))
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def store_id() -> int:
    with engine.begin() as conn:
        return conn.scalar(
            insert(Store).values(code="TST-01", name="Test Store", city="Bengaluru").returning(Store.id)
        )


@pytest.fixture
def make_product():
    """Create a product and its stock row in the test store."""
    def _make(store_id: int, sku: str, on_hand: int, price: str = "100.00", reserved: int = 0,
              name: str | None = None, category: str = "tools", aisle: str = "1", bay: str = "A01"):
        with engine.begin() as conn:
            conn.execute(insert(Product).values(
                sku=sku, name=name or f"Test product {sku}", category=category,
                price=Decimal(price)))
            conn.execute(insert(Stock).values(
                store_id=store_id, sku=sku, on_hand=on_hand, reserved=reserved,
                reorder_point=0, aisle=aisle, bay=bay))
        return sku
    return _make


@pytest.fixture
def stock_of():
    """Read a stock row straight from the database (not through the API)."""
    def _read(store_id: int, sku: str) -> dict:
        with engine.connect() as conn:
            row = conn.execute(
                select(Stock.on_hand, Stock.reserved).where(
                    Stock.store_id == store_id, Stock.sku == sku)
            ).one()
        return {"on_hand": row.on_hand, "reserved": row.reserved,
                "available": row.on_hand - row.reserved}
    return _read


@pytest.fixture
def live_server():
    """A real uvicorn server on a free port, in a background thread.

    TestClient calls the app directly, one request at a time. The
    concurrency tests need genuinely simultaneous HTTP requests, the way
    real clients send them, so they talk to this server instead.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("live server did not start")
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    app.dependency_overrides.clear()
