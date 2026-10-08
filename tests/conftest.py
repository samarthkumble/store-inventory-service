"""Shared test setup.

Tests run against a REAL Postgres, in a separate database (inventory_test),
so they never touch your dev data. Never SQLite: SQLite effectively runs one
writer at a time, so concurrency bugs would simply disappear in tests.
"""
import os

# Must happen before ANY app import: app.core.config reads DATABASE_URL once,
# at import time. Environment variables win over the .env file.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://inventory:inventory@localhost:5432/inventory_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, insert, make_url, select, text  # noqa: E402

from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Product, Stock, Store  # noqa: E402

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
    def _make(store_id: int, sku: str, on_hand: int, price: str = "100.00", reserved: int = 0):
        with engine.begin() as conn:
            conn.execute(insert(Product).values(
                sku=sku, name=f"Test product {sku}", category="tools", price=Decimal(price)))
            conn.execute(insert(Stock).values(
                store_id=store_id, sku=sku, on_hand=on_hand, reserved=reserved,
                reorder_point=0, aisle="1", bay="A01"))
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
