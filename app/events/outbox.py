"""Recording StockChanged events (inside the caller's transaction)."""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import OutboxEvent

# Bump when the payload shape changes, so consumers can handle old and new
# events side by side during a rollout (schema versioning).
EVENT_VERSION = 1


def record_stock_event(
    db: Session,
    event_type: str,
    store_id: int,
    sku: str,
    qty: int,
    on_hand: int,
    reserved: int,
    order_id: uuid.UUID | None = None,
) -> None:
    """Queue a StockChanged event. It's only saved if the caller commits.

    event_type is RESERVED, CONFIRMED, CANCELLED or RECEIVED. The event carries
    the stock levels AFTER the change, so consumers never need to call back
    into this service (they stay decoupled even if it is down).
    """
    db.add(OutboxEvent(
        stream=settings.stock_events_stream,
        payload={
            "v": EVENT_VERSION,
            # Unique id: lets consumers detect and skip duplicate deliveries.
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "store_id": store_id,
            "sku": sku,
            "qty": qty,
            "on_hand": on_hand,
            "reserved": reserved,
            "available": on_hand - reserved,
            "order_id": str(order_id) if order_id else "",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    ))
