"""Application level errors.

`AppError` is raised for any situation described in the spec's "Errors"
section: malformed input, values outside allowed sets, missing fields,
negative amounts, decisions naming nothing pending, and decisions from the
wrong role. Callers decide how to surface it (CLI exits non zero with the
message, the HTTP API returns a 4xx response with the message).
"""

from __future__ import annotations


class AppError(Exception):
    """A clear, user facing error. Never represents a bug in the program."""


class NotFoundError(AppError):
    """Raised when a lookup (request, approval, ...) does not exist."""
