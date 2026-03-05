"""Custom exceptions for fastmail-cli."""


class JMAPError(Exception):
    """Base exception for JMAP errors."""
    pass


class AuthenticationError(JMAPError):
    """Raised when authentication fails."""
    pass


class ValidationError(JMAPError):
    """Raised when input validation fails."""
    pass


class NotFoundError(JMAPError):
    """Raised when a requested resource is not found."""
    pass
