"""Importing this package registers every table on Base.metadata.

Alembic's env.py imports it, so all models are known when it compares
the code against the database.
"""
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.stock import Stock
from app.models.store import Store

__all__ = ["Order", "OrderItem", "Product", "Stock", "Store"]
