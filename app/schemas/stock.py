from datetime import datetime
from decimal import Decimal

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class StoreOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    city: str


class StockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    store_id: int
    sku: str
    name: str
    on_hand: int
    reserved: int
    # Calculated in the query (on_hand - reserved), never stored.
    available: int
    reorder_point: int
    aisle: str
    bay: str
    updated_at: datetime


class ProductSearchResult(BaseModel):
    """One search hit: what a shopper needs to find the item in this store."""

    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    category: str
    price: Decimal
    available: int
    aisle: str
    bay: str


class StockReceive(BaseModel):
    """A delivery from a supplier or distribution centre."""

    model_config = ConfigDict(extra="forbid")

    qty: Annotated[int, Field(gt=0, le=10_000)]
