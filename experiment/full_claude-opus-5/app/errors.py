"""Errors that reach the user as a message, never as a traceback."""


class AppError(Exception):
    """Anything the operator did wrong, or any input we refuse."""


class OperationFailure(AppError):
    """An operation refused to run against the account it was given."""
