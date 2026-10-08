"""HTTP layer for the catalogue: parse the request, call the service, return.

No SQL and no business rules here. Errors raised by services become HTTP
responses through the handlers registered in main.py.
"""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.product import (
    SKU_PATTERN,
    Category,
    ProductCreate,
    ProductOut,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter(prefix="/products", tags=["products"])

DbSession = Annotated[Session, Depends(get_db)]
SkuPath = Annotated[str, Path(pattern=SKU_PATTERN, examples=["PLB-00024"])]


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, db: DbSession):
    return product_service.create_product(db, data)


@router.get("", response_model=list[ProductOut])
def list_products(
    db: DbSession,
    category: Category | None = None,
    # Always paginate: an unbounded list endpoint gets slower as data grows.
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return product_service.list_products(db, category, limit, offset)


@router.get("/{sku}", response_model=ProductOut)
def get_product(sku: SkuPath, db: DbSession):
    return product_service.get_product(db, sku)


@router.patch("/{sku}", response_model=ProductOut)
def update_product(sku: SkuPath, data: ProductUpdate, db: DbSession):
    return product_service.update_product(db, sku, data)


@router.delete("/{sku}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(sku: SkuPath, db: DbSession):
    product_service.delete_product(db, sku)
