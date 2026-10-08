"""API shapes for products: what clients send and what we promise back.

These are separate from the SQLAlchemy models on purpose. The model is how
data is *stored*; the schema is the API *contract*. They can change
independently without breaking each other.
"""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A fixed list shows up as a dropdown in /docs and rejects typos like "paints".
# Trade-off: adding a category needs a code change. A real catalogue with
# hundreds of categories would keep them in their own table instead.
Category = Literal["paint", "plumbing", "tools", "electrical", "garden"]

SKU_PATTERN = r"^[A-Z]{3}-\d{5}$"
Sku = Annotated[str, Field(pattern=SKU_PATTERN, examples=["PLB-00024"])]
Name = Annotated[str, Field(min_length=1, max_length=200)]
# Decimal, not float. Pydantic sends it in JSON as a string ("1249.00") so no
# client parses it into an inexact float by accident.
Price = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2, examples=["1249.00"])]


class ProductCreate(BaseModel):
    # Unknown fields are an error, not silently ignored: a typo like "prise"
    # gets a clear 422 instead of a product saved without the change.
    model_config = ConfigDict(extra="forbid")

    sku: Sku
    name: Name
    category: Category
    price: Price


class ProductUpdate(BaseModel):
    """PATCH body: send only the fields you want to change.

    The SKU can't be changed: it's the product's identity, and old orders
    refer to it. extra="forbid" turns an attempt into a 422.
    """

    model_config = ConfigDict(extra="forbid")

    name: Name | None = None
    category: Category | None = None
    price: Price | None = None

    @field_validator("name", "category", "price")
    @classmethod
    def reject_null(cls, value):
        # Leaving a field out means "don't change it". Sending null explicitly
        # would try to blank a required column, so it's rejected here with a
        # clear 422 instead of failing in the database with a 500.
        if value is None:
            raise ValueError("may be omitted, but not set to null")
        return value


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    category: str
    price: Decimal
    created_at: datetime
    updated_at: datetime
