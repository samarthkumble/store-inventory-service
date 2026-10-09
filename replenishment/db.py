"""The replenishment service's own tables, in its own schema."""
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, MetaData, String, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from replenishment.config import settings

SCHEMA = "replenishment"


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention={
        "pk": "pk_%(table_name)s",
    })


class StockLevel(Base):
    """Latest known stock for each store/SKU, as reported by events."""

    __tablename__ = "stock_levels"

    store_id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(20), primary_key=True)
    on_hand: Mapped[int]
    reserved: Mapped[int]
    available: Mapped[int]
    # Time of the event this row came from. Used to ignore an older event
    # that arrives after a newer one (out-of-order delivery).
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyDemand(Base):
    """Units sold (confirmed orders) per store, SKU and store-local day."""

    __tablename__ = "daily_demand"

    store_id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(20), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    units: Mapped[int]


class ProcessedEvent(Base):
    """Every event id handled so far. Makes redelivered events harmless."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create the schema and tables if missing.

    Kept deliberately simple for this add-on service. A production service
    would own its own Alembic migrations, exactly like the inventory service.
    """
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    Base.metadata.create_all(engine)
