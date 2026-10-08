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
