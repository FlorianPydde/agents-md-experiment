"""Custom errors for the application.

All application-level errors derive from AppError. The CLI entry point
catches AppError and prints a clean message instead of a traceback.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected application errors."""


class ValidationError(AppError):
    """Raised when input data (files, requests, decisions) is malformed."""


class NotFoundError(AppError):
    """Raised when a lookup (request, approval) fails to find its target."""


class OperationFailure(Exception):
    """Raised internally by an operation when it cannot complete.

    This is caught by the engine and turned into a `failed` request state
    plus a log entry - it is not a program-level error.
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
