"""Settings for the replenishment service (separate from the inventory service's)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same Postgres server, but this service only ever touches its own
    # "replenishment" schema. It learns about stock purely from events.
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    stock_events_stream: str = "stock-events"
    # A consumer group tracks which events this service has handled. Several
    # consumer processes in one group share the work (each event goes to one).
    consumer_group: str = "replenishment"
    consumer_name: str = "replenishment-1"

    # Reorder policy: suggest a reorder when
    #   available < avg_daily_demand * lead_time_days + safety_stock
    lead_time_days: int = 3
    safety_stock: int = 5
    demand_window_days: int = 7       # moving-average window
    store_timezone: str = "Asia/Kolkata"  # "a day" means a store day, not a UTC day


settings = Settings()
