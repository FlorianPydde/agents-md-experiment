"""Error types that surface to the user as a clean message, never a traceback."""

from __future__ import annotations


class AppError(Exception):
    """Base class for every user facing error."""


class InputError(AppError):
    """A file is missing or malformed, or a value is outside the allowed set."""


class NotFoundError(AppError):
    """A reference or resource was named that does not exist."""


class DecisionError(AppError):
    """A decision could not be applied to a pending approval."""


class OperationError(AppError):
    """An operation refused to run against the world state."""
