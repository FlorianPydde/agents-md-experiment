"""Application level errors.

All expected failure conditions raise ``AppError`` so the CLI entry point can
print a clear, single line message and exit with a non zero status instead of
letting a traceback escape.
"""

from __future__ import annotations


class AppError(Exception):
    """Raised for any expected, user facing failure."""


class OperationError(AppError):
    """Raised when an operation's arguments are unacceptable or it fails."""
