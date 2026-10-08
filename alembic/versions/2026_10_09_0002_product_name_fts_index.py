"""full-text search index on products.name

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09

A separate migration on purpose: a real schema evolves through small,
reviewable steps, and every database that already ran 0001 just applies this.

On a big live table you would use CREATE INDEX CONCURRENTLY so writes aren't
blocked while the index builds (it can't run inside a transaction, so it
needs its own non-transactional migration). With 200 rows a plain CREATE
INDEX takes milliseconds.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_products_name_fts",
        "products",
        [sa.text("to_tsvector('english'::regconfig, name)")],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_products_name_fts", table_name="products")
