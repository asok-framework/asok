class RedirectException(Exception):
    """Exception raised by request.redirect() to abort processing and send a redirect."""

    def __init__(self, url: str, status: int = 302):
        self.url: str = url
        self.status: int = status


class AbortException(Exception):
    """Exception raised by request.abort() to stop execution with a specific HTTP status."""

    def __init__(self, status: int, message: str = None):
        self.status: int = status
        self.message: str = message
