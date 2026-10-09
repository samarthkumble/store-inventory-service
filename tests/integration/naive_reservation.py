"""THE BROKEN VERSION. Kept in tests/ only, to prove the bug is real.

This is how reservation code usually looks the first time it's written:
load the row, check in Python, change the attribute, commit. It reads
fine and passes every single-user test, but under concurrency it oversells.
"""
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError, OutOfStockError
from app.models import Stock


def reserve_naive(db: Session, store_id: int, sku: str, qty: int) -> tuple[int, int]:
    stock = db.get(Stock, (store_id, sku))           # 1. READ  (no lock)
    if stock is None:
        raise NotFoundError(f"SKU {sku} is not stocked at store {store_id}")
    available = stock.on_hand - stock.reserved
    if available < qty:                              # 2. CHECK (on a value that may be stale)
        raise OutOfStockError(store_id, sku, qty, available)
    stock.reserved = stock.reserved + qty            # 3. WRITE an absolute value:
    # the ORM sends "UPDATE stock SET reserved = 7", not "reserved = reserved + 1".
    # If 5 requests all read reserved = 6, all 5 write 7. Four reservations
    # vanish (lost updates), all 5 customers get a 201, and the row still
    # looks perfectly legal, so no CHECK constraint can catch it.
    return stock.on_hand, stock.reserved
