"""Store-scoped endpoints. Stock and shelf location only make sense inside one store."""
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.product import SKU_PATTERN, Category
from app.schemas.stock import ProductSearchResult, StockOut, StoreOut
from app.services import stock_service

router = APIRouter(prefix="/stores", tags=["stores"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=list[StoreOut])
def list_stores(db: DbSession):
    return stock_service.list_stores(db)


@router.get("/{store_id}/stock/{sku}", response_model=StockOut)
def get_stock(
    store_id: int,
    sku: Annotated[str, Path(pattern=SKU_PATTERN, examples=["PLB-00024"])],
    db: DbSession,
):
    """On-hand, reserved and available (= on_hand - reserved) for one SKU, plus its shelf location."""
    return stock_service.get_stock(db, store_id, sku)


@router.get("/{store_id}/products", response_model=list[ProductSearchResult])
def search_products(
    store_id: int,
    db: DbSession,
    q: Annotated[
        str | None,
        Query(min_length=1, max_length=100, examples=["pillar tap"],
              description="Words to search for in product names. Omit to list everything."),
    ] = None,
    category: Category | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Find products in one store, most relevant first, with aisle and bay."""
    return stock_service.search_products(db, store_id, q, category, limit, offset)
