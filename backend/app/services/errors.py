class NotFoundError(Exception):
    """Raised when a scoped resource does not exist for the current workspace."""


class NotAuthorizedError(Exception):
    """Raised when a resource belongs to a different workspace (no-op boundary)."""


class NotAuthenticatedError(Exception):
    """Raised when an operation runs without a resolved authenticated identity.

    The auth dependency normally stamps identity before any route executes, so
    reaching this error means a code path bypassed authentication. It maps to
    HTTP 401 and must never fall back to a shared workspace (fail closed).
    """


class InvalidCredentialsError(Exception):
    """Raised when an email/password pair does not match any account (HTTP 401)."""


class EmailTakenError(Exception):
    """Raised when signup targets an email that already exists (HTTP 409)."""