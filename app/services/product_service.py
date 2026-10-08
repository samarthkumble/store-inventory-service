"""Business logic for the product catalogue. No HTTP code here."""
from psycopg.errors import UniqueViolation
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import ConflictError, NotFoundError
from app.models import Product
from app.schemas.product import Category, ProductCreate, ProductUpdate


def create_product(db: Session, data: ProductCreate) -> Product:
    product = Product(**data.model_dump())
    db.add(product)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # We don't check "does this SKU exist?" first and then insert: two
        # requests could both pass the check and both insert (a race). The
        # primary key makes the database the single judge, and we translate
        # its answer. This includes soft-deleted SKUs: they're still taken.
        if isinstance(exc.orig, UniqueViolation):
            raise ConflictError(f"Product {data.sku} already exists") from exc
        raise
    return product


def get_product(db: Session, sku: str) -> Product:
    product = db.scalar(select(Product).where(Product.sku == sku, Product.is_active))
    if product is None:
        raise NotFoundError(f"Product {sku} not found")
    return product


def list_products(
    db: Session, category: Category | None, limit: int, offset: int
) -> list[Product]:
    stmt = select(Product).where(Product.is_active)
    if category is not None:
        stmt = stmt.where(Product.category == category)
    # A stable ORDER BY is required for pagination: without it, Postgres may
    # return rows in a different order on each page, repeating or skipping some.
    stmt = stmt.order_by(Product.sku).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def update_product(db: Session, sku: str, data: ProductUpdate) -> Product:
    product = get_product(db, sku)
    # exclude_unset: only the fields the client actually sent.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    db.commit()
    return product


def delete_product(db: Session, sku: str) -> None:
    """Soft delete: hide the product but keep it, because old orders refer to it."""
    product = get_product(db, sku)
    product.is_active = False
    db.commit()
