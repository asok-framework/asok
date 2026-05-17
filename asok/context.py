import contextvars
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    from .request import Request

# Thread-safe global request context
request_var: contextvars.ContextVar[Optional["Request"]] = contextvars.ContextVar(
    "request", default=None
)


@contextmanager
def request_context(request: "Request") -> Iterator[None]:
    """Context manager to set and automatically cleanup request context.

    Usage:
        with request_context(request):
            # request is available via request_var.get()
            pass
        # request_var is automatically cleaned up
    """
    token = request_var.set(request)
    try:
        yield
    finally:
        request_var.reset(token)
