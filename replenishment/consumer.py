"""Consumes StockChanged events from the Redis Stream.

Run as its own process:   python -m replenishment.consumer

Flow per event: process it in ONE database transaction, then XACK it.
- Crash before the commit: nothing was saved; the event is still pending in
  Redis and is delivered again.
- Crash after the commit but before XACK: the event is delivered again, but
  its event_id is already in processed_events, so it's skipped.
Either way each event affects the data exactly once.
"""
import logging
import signal
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import redis
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from replenishment.config import settings
from replenishment.db import DailyDemand, ProcessedEvent, SessionLocal, StockLevel

log = logging.getLogger("replenishment-consumer")
STORE_TZ = ZoneInfo(settings.store_timezone)


def ensure_group(r: redis.Redis) -> None:
    """Create the consumer group (and the stream) if they don't exist yet."""
    try:
        # id="0": a brand-new group starts from the beginning of the stream.
        r.xgroup_create(settings.stock_events_stream, settings.consumer_group,
                        id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):  # BUSYGROUP = already exists, fine
            raise


def handle_event(db: Session, fields: dict[str, str]) -> bool:
    """Apply one event. Returns False if it was a duplicate (already processed)."""
    # Idempotency check and the work below commit together, so "processed"
    # can never be recorded without the effect (or the other way round).
    first_time = db.scalar(
        insert(ProcessedEvent)
        .values(event_id=uuid.UUID(fields["event_id"]))
        .on_conflict_do_nothing()
        .returning(ProcessedEvent.event_id)
    )
    if first_time is None:
        return False

    store_id, sku = int(fields["store_id"]), fields["sku"]
    occurred_at = datetime.fromisoformat(fields["occurred_at"])

    # Upsert the latest stock level, but never let an OLDER event overwrite a
    # newer one (streams can redeliver an old event after newer ones).
    values = {"store_id": store_id, "sku": sku, "on_hand": int(fields["on_hand"]),
              "reserved": int(fields["reserved"]), "available": int(fields["available"]),
              "as_of": occurred_at}
    stmt = insert(StockLevel).values(**values)
    db.execute(stmt.on_conflict_do_update(
        index_elements=[StockLevel.store_id, StockLevel.sku],
        set_={k: stmt.excluded[k] for k in ("on_hand", "reserved", "available", "as_of")},
        where=StockLevel.as_of <= stmt.excluded.as_of,
    ))

    # Demand = units actually sold, counted on the store's local calendar day.
    if fields["event_type"] == "CONFIRMED":
        day = occurred_at.astimezone(STORE_TZ).date()
        stmt = insert(DailyDemand).values(store_id=store_id, sku=sku, day=day,
                                          units=int(fields["qty"]))
        db.execute(stmt.on_conflict_do_update(
            index_elements=[DailyDemand.store_id, DailyDemand.sku, DailyDemand.day],
            set_={"units": DailyDemand.units + stmt.excluded.units},
        ))
    return True


def consume_once(r: redis.Redis, block_ms: int = 1000, count: int = 100) -> int:
    """Handle this consumer's pending events, then new ones. Returns events handled."""
    handled = 0
    # "0"  = events delivered to us before but never acknowledged (we crashed).
    # ">"  = events never delivered to anyone in this group.
    for start, block in (("0", None), (">", block_ms)):
        response = r.xreadgroup(settings.consumer_group, settings.consumer_name,
                                {settings.stock_events_stream: start},
                                count=count, block=block)
        for _stream, messages in response or []:
            for message_id, fields in messages:
                if not fields:  # pending entry whose data was trimmed away
                    r.xack(settings.stock_events_stream, settings.consumer_group, message_id)
                    continue
                try:
                    with SessionLocal() as db, db.begin():
                        handle_event(db, fields)
                except Exception:  # noqa: BLE001
                    # Leave it un-acked: it stays pending and is retried next
                    # round. (A production system would move an event that
                    # keeps failing to a dead-letter stream after N attempts.)
                    log.exception("failed to process %s; will retry", message_id)
                    continue
                r.xack(settings.stock_events_stream, settings.consumer_group, message_id)
                handled += 1
    return handled


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    r = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ensure_group(r)
    log.info("consuming %s as %s/%s", settings.stock_events_stream,
             settings.consumer_group, settings.consumer_name)
    while running:
        try:
            handled = consume_once(r)
            if handled:
                log.info("handled %d event(s)", handled)
        except (redis.RedisError, OperationalError, OSError) as exc:
            log.warning("redis or database unavailable (%s); retrying in 2 s", exc)
            time.sleep(2)


if __name__ == "__main__":
    main()
