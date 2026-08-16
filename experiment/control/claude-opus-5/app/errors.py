"""Error types used across the application.

`AppError` is raised for anything the operator did wrong: a missing or malformed
file, a value outside a permitted set, a bad decision.  The command line catches
it and prints a single clear message instead of a traceback.

`OperationFailure` is different: it is a normal, expected outcome of running a
step (for instance debiting more than the balance).  It moves a request to
`failed` rather than stopping the program.
"""


class AppError(Exception):
    """A problem with the input or the request the caller made."""


class OperationFailure(Exception):
    """An operation could not be applied to the world."""
