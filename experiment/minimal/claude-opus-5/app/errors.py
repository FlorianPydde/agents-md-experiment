"""Errors that are reported to the caller as a message, never as a traceback."""


class AppError(Exception):
    """A problem the caller can understand and act on."""


class InputError(AppError):
    """External data is missing, malformed, or outside the allowed sets."""


class UsageError(AppError):
    """The command was asked to do something that cannot be done."""
