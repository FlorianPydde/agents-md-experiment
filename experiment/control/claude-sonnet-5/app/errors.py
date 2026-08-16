"""Shared error type for user facing failures.

Any code that detects a problem the operator caused (a malformed file, an
out of range value, a decision naming the wrong role, and so on) raises
``AppError``. The CLI entry point catches it, prints a clear one line
message to stderr, and exits with a non zero status -- never a traceback.
"""


class AppError(Exception):
    """A problem that should be reported to the operator, not a traceback."""
