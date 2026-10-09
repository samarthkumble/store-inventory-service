"""App settings, read from environment variables (or the .env file).

The same code runs on your laptop, in Docker and in GitHub Actions;
only the environment changes. This is the "12-factor app" config rule.
"""
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required: the app refuses to start if DATABASE_URL is missing,
    # instead of failing later on the first query.
    database_url: str

    # How POST /orders reserves stock. Both options are correct under
    # concurrency; see app/services/reservation.py for the trade-offs.
    reservation_strategy: Literal["atomic", "for_update"] = "atomic"

    # Redis: the event stream (Milestone 4) and the product cache.
    redis_url: str = "redis://localhost:6379/0"
    stock_events_stream: str = "stock-events"
    product_cache_ttl_seconds: int = 300


settings = Settings()
