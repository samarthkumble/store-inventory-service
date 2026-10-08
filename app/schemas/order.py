from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.schemas.product import Sku


class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    # Upper limit stops a typo like 10000 from reserving a store's whole stock.
    qty: Annotated[int, Field(gt=0, le=1000)]


class OrderCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {"store_id": 1, "items": [{"sku": "PLB-00024", "qty": 2},
                                          {"sku": "PLB-00037", "qty": 1}]}
            ]
        },
    )

    store_id: Annotated[int, Field(gt=0)]
    # Bounded so one request can't hold locks on hundreds of rows.
    items: Annotated[list[OrderItemIn], Field(min_length=1, max_length=50)]


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    qty: int
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: int
    status: str
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut]

    @computed_field
    @property
    def total(self) -> Decimal:
        # Uses the price snapshot on each line, not today's product price.
        return sum((i.unit_price * i.qty for i in self.items), Decimal("0.00"))
