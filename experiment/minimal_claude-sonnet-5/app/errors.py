"""Application-level errors.

An `AppError` is any error condition that should be reported to the user as a
clear message and a non-zero exit status, never as a Python traceback.
"""

from __future__ import annotations


class AppError(Exception):
    """Raised for any user-facing failure: bad input, missing data, policy
    violations at the edge, etc. The CLI entry point catches this class and
    prints its message without a traceback."""
