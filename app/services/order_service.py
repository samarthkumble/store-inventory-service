"""Orders: reserve stock, then confirm (sold) or cancel (released).

Lifecycle:  RESERVED --confirm--> CONFIRMED   (on_hand -= qty, reserved -= qty)
            RESERVED --cancel---> CANCELLED   (reserved -= qty)
Both end states are final.

Lock order rule (prevents deadlocks): every transaction that touches several
rows locks them in the same global order: the order row first (if any),
then stock rows sorted by SKU.
"""
from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import InvalidOrderStateError, NotFoundError
from app.models import Order, OrderItem, Product, Stock, Store
from app.schemas.order import OrderCreate
from app.services.reservation import ReserveFn


def create_order(db: Session, data: OrderCreate, reserve: ReserveFn) -> Order:
    """Reserve every line in ONE transaction: all items succeed, or none do."""
    try:
        if db.get(Store, data.store_id) is None:
            raise NotFoundError(f"Store {data.store_id} not found")

        # Merge duplicate lines ("tap x1, tap x2" becomes "tap x3"):
        # order_items allows each SKU once per order.
        quantities: dict[str, int] = defaultdict(int)
        for item in data.items:
            quantities[item.sku] += item.qty
        # Sorted, so two orders for the same SKUs always lock rows in the
        # same order. Without this, order A (X then Y) and order B (Y then X)
        # can each hold one lock and wait forever for the other: a deadlock.
        skus = sorted(quantities)

        # Snapshot prices before locking anything (keeps lock time short).
        prices = {
            sku: price
            for sku, price in db.execute(
                select(Product.sku, Product.price).where(
                    Product.sku.in_(skus), Product.is_active
                )
            )
        }
        missing = [sku for sku in skus if sku not in prices]
        if missing:
            raise NotFoundError(f"Unknown product(s): {', '.join(missing)}")

        for sku in skus:
            reserve(db, data.store_id, sku, quantities[sku])

        order = Order(
            store_id=data.store_id,
            status="RESERVED",
            items=[
                OrderItem(sku=sku, qty=quantities[sku], unit_price=prices[sku])
                for sku in skus
            ],
        )
        db.add(order)
        db.commit()
        return order
    except Exception:
        # Undo every reservation made so far in this transaction.
        db.rollback()
        raise


def get_order(db: Session, order_id: UUID) -> Order:
    order = db.scalar(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.id == order_id)
        # Reload from the database even if this session has a cached copy.
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise NotFoundError(f"Order {order_id} not found")
    return order


def confirm_order(db: Session, order_id: UUID) -> Order:
    """The customer paid: the units leave the shelf."""
    return _finish_order(
        db, order_id, "CONFIRMED", "confirmed",
        lambda qty: {"on_hand": Stock.on_hand - qty, "reserved": Stock.reserved - qty},
    )


def cancel_order(db: Session, order_id: UUID) -> Order:
    """The reservation is released: the units become available again."""
    return _finish_order(
        db, order_id, "CANCELLED", "cancelled",
        lambda qty: {"reserved": Stock.reserved - qty},
    )


def _finish_order(db, order_id, new_status, verb, stock_change) -> Order:
    try:
        # A conditional update, just like reserve_atomic: "change the status
        # only if it is still RESERVED". If a user double-clicks "confirm",
        # the second request waits on the first one's row lock, then sees
        # status = CONFIRMED, updates 0 rows and gets a 409. Stock is
        # therefore never decremented twice.
        store_id = db.scalar(
            update(Order)
            .where(Order.id == order_id, Order.status == "RESERVED")
            .values(status=new_status, updated_at=func.now())
            .returning(Order.store_id)
            .execution_options(synchronize_session=False)
        )
        if store_id is None:
            current = db.scalar(select(Order.status).where(Order.id == order_id))
            if current is None:
                raise NotFoundError(f"Order {order_id} not found")
            raise InvalidOrderStateError(
                f"Order {order_id} is {current}; only RESERVED orders can be {verb}"
            )

        items = db.execute(
            select(OrderItem.sku, OrderItem.qty)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.sku)  # same lock order as create_order
        ).all()
        for sku, qty in items:
            result = db.execute(
                update(Stock)
                .where(Stock.store_id == store_id, Stock.sku == sku)
                .values(**stock_change(qty), updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                # Should be impossible: the reservation created this row's
                # "reserved" units. Fail loudly instead of silently drifting.
                raise RuntimeError(f"Stock row missing for {sku} at store {store_id}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_order(db, order_id)
