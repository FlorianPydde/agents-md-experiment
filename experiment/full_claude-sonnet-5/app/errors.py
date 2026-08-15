"""Application-level errors that map to clean CLI exits."""


class AppError(Exception):
    """A user-facing error. The CLI catches this and exits non-zero with the message."""
