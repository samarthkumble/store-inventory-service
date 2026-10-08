from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.order import OrderCreate, OrderOut
from app.services import order_service
from app.services.reservation import ReserveFn, get_reserve_strategy

router = APIRouter(prefix="/orders", tags=["orders"])

DbSession = Annotated[Session, Depends(get_db)]


@router.post(
    "",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Not enough stock for at least one item. Nothing was reserved."}},
)
def create_order(
    data: OrderCreate,
    db: DbSession,
    reserve: Annotated[ReserveFn, Depends(get_reserve_strategy)],
):
    """Reserve stock for every item at one store, all or nothing."""
    return order_service.create_order(db, data, reserve)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: UUID, db: DbSession):
    return order_service.get_order(db, order_id)


@router.post("/{order_id}/confirm", response_model=OrderOut,
             responses={409: {"description": "Order is not RESERVED."}})
def confirm_order(order_id: UUID, db: DbSession):
    """Sell the reserved units: on_hand -= qty, reserved -= qty."""
    return order_service.confirm_order(db, order_id)


@router.post("/{order_id}/cancel", response_model=OrderOut,
             responses={409: {"description": "Order is not RESERVED."}})
def cancel_order(order_id: UUID, db: DbSession):
    """Release the reserved units: reserved -= qty."""
    return order_service.cancel_order(db, order_id)
