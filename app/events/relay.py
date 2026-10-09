"""Outbox relay: publishes saved events to the Redis Stream.

Run as its own process:   python -m app.events.relay

Delivery guarantee: AT-LEAST-ONCE. If Redis accepts a batch but the process
dies before marking it published, that batch is sent again on restart.
Consumers handle this by skipping event ids they've already processed
(see replenishment/consumer.py). "At-least-once + idempotent consumer" is the
standard way to get effectively-once processing.
"""
import logging
import signal
import time
from datetime import datetime, timezone

import redis
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import OutboxEvent

log = logging.getLogger("outbox-relay")

# Cap the stream's length so Redis memory can't grow forever ("~" = approximate,
# which is much cheaper). Kafka's equivalent is a topic retention period.
STREAM_MAXLEN = 100_000


def _as_fields(payload: dict) -> dict[str, str]:
    # Redis Stream entries are flat string->string maps.
    return {key: "" if value is None else str(value) for key, value in payload.items()}


def relay_once(db: Session, redis_client: redis.Redis, batch_size: int = 100) -> int:
    """Publish up to batch_size unpublished events, oldest first. Returns how many."""
    events = list(db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.id)
        .limit(batch_size)
        # SKIP LOCKED: if a second relay is running, it skips rows this one
        # holds instead of waiting or double-publishing them.
        .with_for_update(skip_locked=True)
    ))
    if not events:
        db.rollback()
        return 0

    pipe = redis_client.pipeline(transaction=False)  # one network round trip
    for event in events:
        pipe.xadd(event.stream, _as_fields(event.payload), maxlen=STREAM_MAXLEN, approximate=True)
    pipe.execute()

    now = datetime.now(timezone.utc)
    for event in events:
        event.published_at = now
    db.commit()
    return len(events)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    running = True

    def stop(*_):
        nonlocal running
        running = False  # finish the current batch, then exit cleanly

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log.info("relaying outbox -> %s", settings.stock_events_stream)
    while running:
        try:
            with SessionLocal() as db:
                published = relay_once(db, redis_client)
            if published:
                log.info("published %d event(s)", published)
            else:
                time.sleep(0.5)  # idle: poll twice a second
        except (redis.RedisError, OperationalError, OSError) as exc:
            log.warning("redis or database unavailable (%s); retrying in 2 s", exc)
            time.sleep(2)


if __name__ == "__main__":
    main()
