"""Errors that reach the user, and the failure an operation raises."""


class ServiceRequestError(Exception):
    """A condition the caller must fix. Reported as a message, never a traceback."""


class UnknownReferenceError(ServiceRequestError):
    """A reference that names nothing the system knows about."""


class OperationFailure(Exception):
    """An operation refused to run or could not complete. Fails the request."""
