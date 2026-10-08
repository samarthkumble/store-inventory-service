"""Domain errors raised by services.

Services describe *what* went wrong in business terms and know nothing about
HTTP. main.py maps each error to a status code in one place. That keeps the
services reusable from a CLI, a test or (later) an event consumer.
"""


class DomainError(Exception):
    """Base class for every expected business error."""


class NotFoundError(DomainError):
    """The thing asked for doesn't exist (or was soft-deleted)."""


class ConflictError(DomainError):
    """The request clashes with current state, e.g. a duplicate SKU."""


class OutOfStockError(ConflictError):
    """Not enough available stock to reserve the requested quantity."""

    def __init__(self, store_id: int, sku: str, requested: int, available: int):
        self.store_id = store_id
        self.sku = sku
        self.requested = requested
        self.available = available
        super().__init__(
            f"Out of stock: {sku} at store {store_id} "
            f"(requested {requested}, available {available})"
        )


class InvalidOrderStateError(ConflictError):
    """The order isn't in a state that allows this action (e.g. confirming twice)."""
