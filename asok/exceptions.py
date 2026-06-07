class AsokException(Exception):
    """Base class for all Asok-related exceptions."""

    pass


class RedirectException(AsokException):
    """Exception raised by request.redirect() to abort processing and send a redirect."""

    def __init__(self, url: str, status: int = 302):
        self.url: str = url
        self.status: int = status
        super().__init__(f"Redirecting to {url}")


class AbortException(AsokException):
    """Exception raised by request.abort() to stop execution with a specific HTTP status."""

    def __init__(self, status: int, message: str = None):
        self.status: int = status
        self.message: str = message
        super().__init__(message or f"HTTP {status}")


# --- Semantic HTTP Exceptions ---


class NotFoundError(AbortException):
    """Shortcut for 404 Not Found."""

    def __init__(self, message: str = "The requested resource was not found"):
        super().__init__(404, message)


class UnauthorizedError(AbortException):
    """Shortcut for 401 Unauthorized."""

    def __init__(
        self, message: str = "Authentication is required to access this resource"
    ):
        super().__init__(401, message)


class ForbiddenError(AbortException):
    """Shortcut for 403 Forbidden."""

    def __init__(
        self, message: str = "You do not have permission to access this resource"
    ):
        super().__init__(403, message)


# --- Logical & Domain Exceptions ---


class SecurityError(AsokException):
    """Raised for security-related issues (CSRF, tampered sessions, etc.)."""

    pass


class TemplateError(AsokException):
    """Raised when an error occurs during template compilation or rendering."""

    pass


class ValidationError(AsokException):
    """Raised when data validation fails."""

    def __init__(self, message: str, errors: dict = None):
        self.errors = errors or {}
        super().__init__(message)


class MailError(AsokException):
    """Raised when an email fails to send."""

    pass


class RateLimitExceeded(AbortException):
    """Raised when a rate limit is exceeded."""

    def __init__(self, message: str = "Too Many Requests", retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(429, message)
