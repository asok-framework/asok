from __future__ import annotations

import functools
import logging
import threading
import time
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .cache import Cache

from .request import Request

_security_logger = logging.getLogger("asok.security")

# SECURITY: Maximum number of tracked buckets in memory to prevent OOM DoS
_MAX_STORE_ENTRIES = 10_000


class RateLimit:
    """Rate limiter middleware and decorator.
    Supports in-memory or backend-based storage (via Cache).
    """

    def __init__(
        self,
        limit: str | int = 60,
        window: Optional[int] = None,
        key_func: Optional[Callable[[Request], str]] = None,
        storage: Optional["Cache"] = None,
        **kwargs,
    ):
        """Initialize the rate limiter.

        Args:
            limit: Maximum requests. Can be an int or string like "60/m", "10/s", "1000/h".
            window: Time window in seconds (only used if limit is an int).
            key_func: Optional function to generate a unique key for the client.
            storage: Optional Cache instance for persistence (allows cross-worker limiting).
        """
        # Support old 'max_requests' parameter name for compatibility
        if "max_requests" in kwargs:
            limit = kwargs["max_requests"]

        if isinstance(limit, str):
            self.max_requests, self.window = self._parse_limit(limit)
        else:
            self.max_requests = limit
            self.window = window or 60

        self.key_func = key_func or self._default_key
        self.storage = storage
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._last_cleanup: float = 0.0

    @staticmethod
    def _parse_limit(limit_str: str) -> tuple[int, int]:
        """Parse limit strings like '60/m' into (count, seconds)."""
        try:
            count_str, period = limit_str.lower().split("/")
            count = int(count_str)
            seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}.get(period[0], 60)
            return count, seconds
        except (ValueError, IndexError, KeyError):
            return 60, 60

    @staticmethod
    def _default_key(request: Request) -> str:
        """Default key generator using the client's IP address."""
        return request.ip or "unknown"

    def __call__(self, request: Request, next_handler: Callable[[Request], Any]) -> Any:
        """Middleware entry point."""
        key = f"rl:{self.key_func(request)}"
        now = time.time()

        if self.storage:
            # Use Cache backend for persistent/shared limiting
            bucket = self.storage.get(key)
            if not bucket or bucket["reset"] <= now:
                bucket = {"count": 0, "reset": now + self.window}

            bucket["count"] += 1
            self.storage.set(key, bucket, ttl=int(bucket["reset"] - now) + 1)
        else:
            # Use local in-memory store (per process)
            with self._lock:
                # Periodic cleanup: remove expired buckets (at most once per window)
                if now - self._last_cleanup >= self.window:
                    self._last_cleanup = now
                    expired = [k for k, v in self._store.items() if v["reset"] <= now]
                    for k in expired:
                        del self._store[k]

                bucket = self._store.get(key)
                if not bucket or bucket["reset"] <= now:
                    # SECURITY: Evict oldest bucket if store is full (prevents OOM DoS)
                    if len(self._store) >= _MAX_STORE_ENTRIES:
                        oldest = min(self._store, key=lambda k: self._store[k]["reset"])
                        del self._store[oldest]
                    bucket = {"count": 0, "reset": now + self.window}
                bucket["count"] += 1
                self._store[key] = bucket

        if bucket["count"] > self.max_requests:
            remaining = int(bucket["reset"] - now)

            # SECURITY AUDIT LOG: Log rate limit hits for monitoring
            _security_logger.warning(
                "[RATE LIMIT] %s %s from %s — limit %d/%ds exceeded (retry in %ds)",
                request.method,
                request.path,
                request.ip,
                self.max_requests,
                self.window,
                remaining,
            )

            request.status_code(429)
            request.content_type = "text/html; charset=utf-8"
            request.environ.setdefault("asok.extra_headers", []).append(
                ("Retry-After", str(remaining))
            )

            app = request.environ.get("asok.app")
            if app and hasattr(app, "_render_error_page"):
                try:
                    return app._render_error_page(request, 429)
                except Exception:
                    pass
            return f"<h1>429 Too Many Requests</h1><p>Retry in {remaining}s.</p>"

        return next_handler(request)


def rate_limit(limit: str | int, window: Optional[int] = None, **kwargs):
    """Decorator to apply rate limiting to a specific route.

    Usage:
        @rate_limit("5/m")
        def get(request):
            return "Hello"
    """
    limiter = RateLimit(limit, window, **kwargs)

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(request, *args, **kw):
            # Middlewares expect (request, next_handler)
            return limiter(request, lambda r: fn(r, *args, **kw))

        return wrapper

    return decorator
