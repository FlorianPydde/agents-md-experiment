"""The single error type the program reports to a caller."""

from __future__ import annotations


class AppError(Exception):
    """A fault the caller can act on. Reported as a message, never a traceback."""
