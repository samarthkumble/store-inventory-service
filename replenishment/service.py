"""Reorder suggestions from the 7-day moving average of daily demand."""
import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from replenishment.config import settings
from replenishment.db import DailyDemand, StockLevel


class ReorderSuggestion(BaseModel):
    store_id: int
    sku: str
    available: int
    avg_daily_demand: float     # units/day over the moving-average window
    reorder_level: float        # avg_daily_demand * lead_time_days + safety_stock
    suggested_qty: int          # brings stock up to reorder_level + one window of demand
    days_of_cover: float | None  # available / avg_daily_demand (None if no demand)


def store_today() -> date:
    return datetime.now(ZoneInfo(settings.store_timezone)).date()


def reorder_suggestions(db: Session, store_id: int, today: date | None = None) -> list[ReorderSuggestion]:
    """Computed on read, so it always reflects the latest events.

    (Storing suggestions would only be worth it if they needed a workflow of
    their own, e.g. a buyer approving or rejecting each one.)
    """
    today = today or store_today()
    window = settings.demand_window_days
    since = today - timedelta(days=window - 1)  # the last `window` days, including today

    units_by_sku = dict(db.execute(
        select(DailyDemand.sku, func.sum(DailyDemand.units))
        .where(DailyDemand.store_id == store_id, DailyDemand.day.between(since, today))
        .group_by(DailyDemand.sku)
    ).tuples().all())

    suggestions = []
    for level in db.scalars(select(StockLevel).where(StockLevel.store_id == store_id)):
        # Days with no sales count as zero, so divide by the full window.
        avg = units_by_sku.get(level.sku, 0) / window
        reorder_level = avg * settings.lead_time_days + settings.safety_stock
        if level.available >= reorder_level:
            continue
        suggestions.append(ReorderSuggestion(
            store_id=store_id,
            sku=level.sku,
            available=level.available,
            avg_daily_demand=round(avg, 2),
            reorder_level=round(reorder_level, 2),
            suggested_qty=math.ceil(reorder_level + avg * window - level.available),
            days_of_cover=round(level.available / avg, 1) if avg else None,
        ))
    # Most urgent first: least cover; items with no demand signal last.
    suggestions.sort(key=lambda s: (s.days_of_cover is None, s.days_of_cover or 0, s.sku))
    return suggestions
