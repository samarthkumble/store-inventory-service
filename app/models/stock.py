from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Stock(Base):
    """One row per product per store. Milestone 2 locks and updates these rows."""

    __tablename__ = "stock"
    __table_args__ = (
        # Safety net (defence in depth): even buggy code can't store
        # impossible stock. These do NOT stop lost updates; the atomic
        # conditional UPDATE in Milestone 2 does.
        CheckConstraint("on_hand >= 0", name="on_hand_nonneg"),
        CheckConstraint("reserved >= 0", name="reserved_nonneg"),
        CheckConstraint("reserved <= on_hand", name="reserved_le_on_hand"),
        CheckConstraint("reorder_point >= 0", name="reorder_point_nonneg"),
    )

    # Composite primary key: the database guarantees one row per (store, sku),
    # and the PK index makes "stock for store X, SKU Y" a single index lookup.
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), primary_key=True)
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    on_hand: Mapped[int] = mapped_column(server_default="0")
    reserved: Mapped[int] = mapped_column(server_default="0")
    reorder_point: Mapped[int] = mapped_column(server_default="0")
    # Shelf location depends on (store, sku), not on the product alone:
    # the same tap can be in aisle 12 in one store and aisle 7 in another.
    aisle: Mapped[str] = mapped_column(String(5))
    bay: Mapped[str] = mapped_column(String(5))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # available = on_hand - reserved is computed in queries, never stored,
    # so there is only one source of truth.
