import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

ORDER_STATUSES = ("RESERVED", "CONFIRMED", "CANCELLED")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        # Allowed moves: RESERVED -> CONFIRMED, RESERVED -> CANCELLED.
        CheckConstraint(
            "status IN ('RESERVED', 'CONFIRMED', 'CANCELLED')", name="status_valid"
        ),
    )

    # UUID, not 1, 2, 3...: IDs appear in URLs, and sequential numbers would let
    # anyone guess other orders and reveal how many orders we take.
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Postgres does NOT index foreign keys automatically, so we add one.
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), index=True)
    status: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (CheckConstraint("qty > 0", name="qty_positive"),)

    # Composite PK: an order can't list the same SKU twice.
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), primary_key=True
    )
    sku: Mapped[str] = mapped_column(ForeignKey("products.sku"), primary_key=True)
    qty: Mapped[int]
    # Price snapshot: a later price change must not rewrite old orders.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(10, 2))

    order: Mapped[Order] = relationship(back_populates="items")
