"""A small Redis cache that the app can live without.

The cache is an optimisation, never a dependency: if Redis is slow or down,
every helper here logs a warning and behaves like a cache miss, so requests
fall back to Postgres instead of failing.
"""
import logging

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import settings

log = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            # Fail fast: a cache that takes seconds to answer is worse than none.
            socket_connect_timeout=0.2,
            socket_timeout=0.2,
            # redis-py retries failed commands 3 times with growing waits by
            # default. Fine for a primary store, wrong for a cache: with Redis
            # down, every product request took seconds longer. One try only.
            retry=Retry(NoBackoff(), 0),
        )
    return _client


def cache_get(key: str) -> str | None:
    try:
        return get_client().get(key)
    except redis.RedisError as exc:
        log.warning("cache get failed for %s: %s", key, exc)
        return None


def cache_set(key: str, value: str, ttl_seconds: int) -> None:
    try:
        get_client().set(key, value, ex=ttl_seconds)
    except redis.RedisError as exc:
        log.warning("cache set failed for %s: %s", key, exc)


def cache_delete(key: str) -> None:
    try:
        get_client().delete(key)
    except redis.RedisError as exc:
        # If this fails the stale entry still expires after its TTL.
        log.warning("cache delete failed for %s: %s", key, exc)
