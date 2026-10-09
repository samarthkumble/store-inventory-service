"""Store-level reads: stock for one SKU, and product search inside a store."""
from sqlalchemy import Row, func, select, update
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.events.outbox import record_stock_event
from app.models import Product, Stock, Store
from app.models.product import ENGLISH, PRODUCT_NAME_TSVECTOR
from app.schemas.product import Category

# available is derived, so it's computed in SQL every time it's read.
AVAILABLE = (Stock.on_hand - Stock.reserved).label("available")


def list_stores(db: Session) -> list[Store]:
    return list(db.scalars(select(Store).order_by(Store.id)))


def _ensure_store_exists(db: Session, store_id: int) -> None:
    # Without this, an unknown store would just return an empty search
    # result, and the client couldn't tell "no matches" from "wrong store".
    if db.get(Store, store_id) is None:
        raise NotFoundError(f"Store {store_id} not found")


def get_stock(db: Session, store_id: int, sku: str) -> Row:
    _ensure_store_exists(db, store_id)
    stmt = (
        select(
            Stock.store_id,
            Stock.sku,
            Product.name,
            Stock.on_hand,
            Stock.reserved,
            AVAILABLE,
            Stock.reorder_point,
            Stock.aisle,
            Stock.bay,
            Stock.updated_at,
        )
        .join(Product, Product.sku == Stock.sku)
        # One lookup on the (store_id, sku) primary key index.
        .where(Stock.store_id == store_id, Stock.sku == sku, Product.is_active)
    )
    row = db.execute(stmt).one_or_none()
    if row is None:
        raise NotFoundError(f"SKU {sku} is not stocked at store {store_id}")
    return row


def receive_stock(db: Session, store_id: int, sku: str, qty: int) -> Row:
    """A delivery arrived: add qty to on_hand (atomically) and emit RECEIVED."""
    _ensure_store_exists(db, store_id)
    try:
        row = db.execute(
            update(Stock)
            .where(Stock.store_id == store_id, Stock.sku == sku)
            .values(on_hand=Stock.on_hand + qty, updated_at=func.now())
            .returning(Stock.on_hand, Stock.reserved)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if row is None:
            raise NotFoundError(f"SKU {sku} is not stocked at store {store_id}")
        record_stock_event(db, "RECEIVED", store_id, sku, qty, row.on_hand, row.reserved)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return get_stock(db, store_id, sku)


def search_products(
    db: Session,
    store_id: int,
    q: str | None,
    category: Category | None,
    limit: int,
    offset: int,
) -> list[Row]:
    _ensure_store_exists(db, store_id)
    stmt = (
        select(
            Product.sku,
            Product.name,
            Product.category,
            Product.price,
            AVAILABLE,
            Stock.aisle,
            Stock.bay,
        )
        .join(Stock, Stock.sku == Product.sku)
        .where(Stock.store_id == store_id, Product.is_active)
    )
    if category is not None:
        stmt = stmt.where(Product.category == category)

    if q:
        # websearch_to_tsquery understands what people type into search boxes:
        # "pillar tap" means both words, "tap -mixer" excludes mixers, and
        # quotes keep an exact phrase. Unlike to_tsquery, it never raises a
        # syntax error on odd input.
        query = func.websearch_to_tsquery(ENGLISH, q)
        stmt = stmt.where(PRODUCT_NAME_TSVECTOR.op("@@")(query)).order_by(
            # Most relevant first; SKU breaks ties so pages stay stable.
            func.ts_rank(PRODUCT_NAME_TSVECTOR, query).desc(),
            Product.sku,
        )
    else:
        stmt = stmt.order_by(Product.sku)

    return list(db.execute(stmt.limit(limit).offset(offset)))
