from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class OutboxEvent(Base):
    """An event waiting to be published (the "transactional outbox" pattern).

    The problem it solves: after changing stock we must also publish an event.
    Committing to Postgres and then calling Redis is two separate systems
    ("dual write"). Crash between the two and the event is lost forever; publish
    first and a rollback leaves an event for a change that never happened.

    Instead the event is INSERTed here, in the SAME transaction as the stock
    change: both are saved or neither is. A separate relay process
    (app/events/relay.py) publishes unpublished rows and marks them done.
    """

    __tablename__ = "outbox"
    __table_args__ = (
        # The relay only ever looks for unpublished rows. A partial index
        # contains just those, so it stays tiny however large the table grows.
        Index("ix_outbox_unpublished", "id", postgresql_where=text("published_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)  # publish order
    stream: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
