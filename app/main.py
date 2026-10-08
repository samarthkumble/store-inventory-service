"""Builds the FastAPI app: routers, error handlers and a health check.

Run locally with:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs
"""
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import SessionLocal
from app.routers import products, stores

app = FastAPI(
    title="Store Inventory & Replenishment Service",
    version="0.1.0",
    description="Per-store stock, shelf locations and product search for a home-improvement retailer.",
)

app.include_router(products.router)
app.include_router(stores.router)


# One place maps business errors to HTTP status codes. Services never import
# FastAPI, and every endpoint answers errors in the same {"detail": ...} shape.
@app.exception_handler(NotFoundError)
def handle_not_found(request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.exception_handler(ConflictError)
def handle_conflict(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness plus a real database round trip. Docker and CI will poll this."""
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
