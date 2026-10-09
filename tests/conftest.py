"""Settings shared by ALL tests (unit and integration).

Must run before any app import: app.core.config reads DATABASE_URL once, at
import time. Environment variables win over the .env file, so tests always
point at the separate inventory_test database, never at your dev data.
"""
import os

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://inventory:inventory@localhost:5432/inventory_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Redis database 15 for tests, so test runs never touch the cache or event
# stream of a dev stack running on the same Redis (which uses database 0).
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")
os.environ["REDIS_URL"] = TEST_REDIS_URL
