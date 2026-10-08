"""The base class every SQLAlchemy model inherits from."""
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Give every constraint and index a predictable name, e.g.
# "ck_stock_reserved_le_on_hand" instead of a random Postgres-generated one.
# Alembic needs real names to drop or change a constraint in a later
# migration, and readable names make database errors easy to understand.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
