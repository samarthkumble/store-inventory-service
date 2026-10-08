"""Reserving stock for one SKU, safely under concurrency.

THE BUG WE ARE PREVENTING (read-then-write):
    1. Request A reads available = 1.     Request B reads available = 1.
    2. A decides "enough", writes reserved = reserved_read + 1.
    3. B decides "enough", writes the same value, overwriting A's write.
    Both succeed, one unit is sold twice, and the stored numbers still look
    legal, so no CHECK constraint fires. This is a "lost update". The naive
    version lives in tests/ (never in app code) so the tests can show it failing.

Two correct strategies follow. Both are called once per SKU, inside the
caller's transaction, and both leave the stock row locked until that
transaction commits or rolls back.
"""
from collections.abc import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError, OutOfStockError
from app.models import Stock

# A strategy takes (db, store_id, sku, qty) and either reserves or raises.
ReserveFn = Callable[[Session, int, str, int], None]


def _raise_unavailable(db: Session, store_id: int, sku: str, qty: int) -> None:
    """Explain why a reservation failed: not carried here, or not enough stock."""
    row = db.execute(
        select(Stock.on_hand, Stock.reserved).where(
            Stock.store_id == store_id, Stock.sku == sku
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"SKU {sku} is not stocked at store {store_id}")
    # Informational only: this value may already be stale by the time the
    # client reads it. The decision itself was made atomically by the UPDATE.
    raise OutOfStockError(store_id, sku, qty, row.on_hand - row.reserved)


def reserve_atomic(db: Session, store_id: int, sku: str, qty: int) -> None:
    """Check and update in ONE statement. This is the default.

    UPDATE stock SET reserved = reserved + :qty
    WHERE store_id = :s AND sku = :k AND on_hand - reserved >= :qty

    Why this can't oversell: UPDATE locks the row. If another transaction is
    already changing it, this one waits. When that one commits, Postgres
    re-checks the WHERE clause against the *new* row before updating
    (even in the default READ COMMITTED isolation level). So the check and
    the write happen as one step, on fresh data, and no one can sneak in
    between them. If the condition is false, 0 rows are updated, and that
    is our "out of stock" signal.
    """
    result = db.execute(
        update(Stock)
        .where(
            Stock.store_id == store_id,
            Stock.sku == sku,
            Stock.on_hand - Stock.reserved >= qty,
        )
        # Raw UPDATEs skip the ORM's onupdate, so set updated_at ourselves.
        .values(reserved=Stock.reserved + qty, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        _raise_unavailable(db, store_id, sku, qty)


def reserve_for_update(db: Session, store_id: int, sku: str, qty: int) -> None:
    """Lock the row first (SELECT ... FOR UPDATE), then decide in Python.

    FOR UPDATE takes the row lock at read time, so any other transaction
    trying to read the same row FOR UPDATE waits until we commit. Read and
    write can no longer interleave, which removes the race.

    Trade-off versus reserve_atomic: two round trips instead of one, and the
    lock is held while Python runs. Use it when the decision needs more than
    one WHERE clause can express (e.g. per-customer limits, calling another
    table), and keep the work between lock and commit short.
    """
    row = db.execute(
        select(Stock.on_hand, Stock.reserved)
        .where(Stock.store_id == store_id, Stock.sku == sku)
        .with_for_update()
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"SKU {sku} is not stocked at store {store_id}")
    available = row.on_hand - row.reserved
    if available < qty:
        raise OutOfStockError(store_id, sku, qty, available)
    db.execute(
        update(Stock)
        .where(Stock.store_id == store_id, Stock.sku == sku)
        .values(reserved=Stock.reserved + qty, updated_at=func.now())
        .execution_options(synchronize_session=False)
    )


STRATEGIES: dict[str, ReserveFn] = {
    "atomic": reserve_atomic,
    "for_update": reserve_for_update,
}


def get_reserve_strategy() -> ReserveFn:
    """FastAPI dependency. Tests override it to swap in other strategies."""
    return STRATEGIES[settings.reservation_strategy]
