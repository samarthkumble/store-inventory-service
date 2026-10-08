from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Numeric,
    String,
    func,
    literal_column,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# 'english' makes Postgres reduce words to their stem ("taps" -> "tap") and
# skip filler words ("a", "for"). The config is written inline as a literal,
# not sent as a query parameter, so the query matches the index expression
# exactly and Postgres can use the index.
FTS_EXPRESSION = "to_tsvector('english'::regconfig, name)"


class Product(Base):
    """Catalogue data: the same in every store. Shelf location lives in Stock."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_nonneg"),
        # Full-text search index. A GIN ("generalized inverted") index maps
        # each word to the rows containing it, like the index at the back of
        # a book. It only helps if the search query uses exactly the same
        # expression, so both come from FTS_EXPRESSION below.
        Index("ix_products_name_fts", text(FTS_EXPRESSION), postgresql_using="gin"),
    )

    # The SKU *is* the product's identity in retail, so it's the primary key.
    sku: Mapped[str] = mapped_column(String(20), primary_key=True)  # e.g. 'PLB-00123'
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(30), index=True)
    # Money is NUMERIC/Decimal, never float: floats can't represent 0.1 exactly.
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    # Soft delete: old orders still reference this product, so we never hard-delete.
    is_active: Mapped[bool] = mapped_column(server_default=true())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # onupdate only fires for ORM updates. Raw UPDATE statements (Milestone 2)
    # must set updated_at themselves.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )



# The same expression, built for queries. stock_service searches with it, and
# test_search checks with EXPLAIN that Postgres really uses the index for it.
ENGLISH = literal_column("'english'::regconfig")
PRODUCT_NAME_TSVECTOR = func.to_tsvector(ENGLISH, Product.name)
