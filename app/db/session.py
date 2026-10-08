"""Database engine and per-request sessions."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# One engine per process. It owns a connection pool, so requests reuse
# open connections instead of opening a new one each time (which is slow).
# pool_pre_ping checks a connection is still alive before handing it out,
# so a database restart doesn't cause "connection closed" errors.
engine = create_engine(settings.database_url, pool_pre_ping=True)

# expire_on_commit=False: objects keep their loaded values after commit,
# so a router can still read them while building the response.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
