import contextvars
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Optional

if TYPE_CHECKING:
    from .request import Request

# Thread-safe global request context
request_var: contextvars.ContextVar[Optional["Request"]] = contextvars.ContextVar(
    "request", default=None
)


class RequestProxy:
    """A proxy that forwards all attribute accesses to the request in the current context."""

    def _get_current_object(self) -> "Request":
        req = request_var.get()
        if req is None:
            raise RuntimeError(
                "Working outside of request context. This occurs when you try to access "
                "the global 'request' object outside of an active HTTP request or WebSocket message handler."
            )
        return req

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_current_object(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._get_current_object(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._get_current_object(), name)

    def __repr__(self) -> str:
        req = request_var.get()
        if req is None:
            return "<RequestProxy [detached]>"
        return repr(req)

    def __str__(self) -> str:
        req = request_var.get()
        if req is None:
            return "Detached Request"
        return str(req)

    def __bool__(self) -> bool:
        return request_var.get() is not None


# Global request proxy object — use `current_request` everywhere outside view functions
current_request = RequestProxy()


@contextmanager
def request_context(request_obj: "Request") -> Iterator[None]:
    """Context manager to set and automatically cleanup request context.

    Usage:
        with request_context(request):
            # request is available via request_var.get()
            pass
        # request_var is automatically cleaned up
    """
    token = request_var.set(request_obj)
    try:
        yield
    finally:
        request_var.reset(token)
