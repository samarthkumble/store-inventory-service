"""App settings, read from environment variables (or the .env file).

The same code runs on your laptop, in Docker and in GitHub Actions;
only the environment changes. This is the "12-factor app" config rule.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Required: the app refuses to start if DATABASE_URL is missing,
    # instead of failing later on the first query.
    database_url: str


settings = Settings()
