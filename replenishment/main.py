"""Replenishment service API.

Run locally:  uvicorn replenishment.main:app --port 8001
"""
from contextlib import asynccontextmanager
from typing import Annotated

import redis
from fastapi import Depends, FastAPI, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from replenishment.config import settings
from replenishment.db import SessionLocal, init_db
from replenishment.service import ReorderSuggestion, reorder_suggestions


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Replenishment Service",
    version="0.1.0",
    description="Consumes StockChanged events and suggests reorders from moving-average demand.",
    lifespan=lifespan,
)


def get_db():
    with SessionLocal() as db:
        yield db


@app.get("/reorder-suggestions", response_model=list[ReorderSuggestion])
def list_reorder_suggestions(
    store_id: Annotated[int, Query(gt=0)],
    db: Annotated[Session, Depends(get_db)],
):
    """SKUs whose available stock is below avg_daily_demand x lead time + safety stock."""
    return reorder_suggestions(db, store_id)


@app.get("/health")
def health() -> dict[str, str]:
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    redis.Redis.from_url(settings.redis_url, socket_timeout=1).ping()
    return {"status": "ok", "database": "ok", "redis": "ok"}
