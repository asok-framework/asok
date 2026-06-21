from __future__ import annotations

import functools
import logging
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .cache import Cache

from .exceptions import RateLimitExceeded as RateLimitExceeded
from .request import Request

_security_logger = logging.getLogger("asok.security")

# SECURITY: Maximum number of tracked buckets in memory to prevent OOM DoS
_MAX_STORE_ENTRIES = 10_000

# Shared store and lock for in-memory limiting across dynamic instances
_local_store: OrderedDict[str, dict[str, Any]] = OrderedDict()
_local_store_lock = threading.Lock()


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
        prefix: str = "rl",
        **kwargs,
    ):
        """Initialize the rate limiter.

        Args:
            limit: Maximum requests. Can be an int or string like "60/m", "10/s", "1000/h".
            window: Time window in seconds (only used if limit is an int).
            key_func: Optional function to generate a unique key for the client.
            storage: Optional Cache instance for persistence (allows cross-worker limiting).
            prefix: Key prefix to segment rate limit buckets (default: "rl").
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
        self.prefix = prefix
        self.storage = self._init_storage(storage)

        self._store = _local_store
        self._lock = _local_store_lock
        self._last_cleanup: float = 0.0

    def _init_storage(self, storage: Optional["Cache"]) -> Optional["Cache"]:
        if storage is not None:
            return storage
        else:
            from .cache import default_cache

            if default_cache.backend in ("redis", "file"):
                return default_cache
            else:
                return None

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

    def _check_storage(self, key: str, now: float) -> dict[str, Any]:
        bucket = self.storage.get(key)
        if not bucket or bucket["reset"] <= now:
            bucket = {"count": 0, "reset": now + self.window}

        bucket["count"] += 1
        self.storage.set(key, bucket, ttl=int(bucket["reset"] - now) + 1)
        return bucket

    def _cleanup_expired_local(self, now: float) -> None:
        if now - self._last_cleanup >= self.window:
            self._last_cleanup = now
            expired = [k for k, v in self._store.items() if v["reset"] <= now]
            for k in expired:
                del self._store[k]

    def _resolve_local_bucket(self, key: str, now: float) -> dict[str, Any]:
        bucket = self._store.get(key)
        if not bucket or bucket["reset"] <= now:
            # SECURITY: Evict oldest bucket if store is full (prevents OOM DoS)
            if len(self._store) >= _MAX_STORE_ENTRIES:
                self._store.popitem(last=False)
            bucket = {"count": 0, "reset": now + self.window}
        return bucket

    def _check_local(self, key: str, now: float) -> dict[str, Any]:
        with self._lock:
            self._cleanup_expired_local(now)
            bucket = self._resolve_local_bucket(key, now)
            bucket["count"] += 1
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = bucket
            return bucket

    def _log_and_raise_exceeded(
        self, request: Request, bucket: dict[str, Any], now: float
    ) -> None:
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

        raise RateLimitExceeded(
            message=f"Too Many Requests. Retry in {remaining}s.",
            retry_after=remaining,
        )

    def check(self, request: Request) -> None:
        """Check the rate limit for the current request.

        Raises RateLimitExceeded if the limit is exceeded.
        """
        key = f"{self.prefix}:{self.key_func(request)}"
        now = time.time()

        if self.storage:
            bucket = self._check_storage(key, now)
        else:
            bucket = self._check_local(key, now)

        if bucket["count"] > self.max_requests:
            self._log_and_raise_exceeded(request, bucket, now)

    def __call__(self, request: Request, next_handler: Callable[[Request], Any]) -> Any:
        """Middleware/decorator entry point."""
        try:
            self.check(request)
        except RateLimitExceeded as e:
            request.status_code(429)
            request.content_type = "text/html; charset=utf-8"
            request.environ.setdefault("asok.extra_headers", []).append(
                ("Retry-After", str(e.retry_after))
            )

            app = request.environ.get("asok.app")
            if app and hasattr(app, "_render_error_page"):
                try:
                    return app._render_error_page(request, 429)
                except Exception:
                    pass
            return f"<h1>429 Too Many Requests</h1><p>Retry in {e.retry_after}s.</p>"

        return next_handler(request)


def rate_limit(limit: str | int, window: Optional[int] = None, **kwargs):
    """Decorator to apply rate limiting to a specific route.

    Usage:
        @rate_limit("5/m")
        def get(request):
            return "Hello"
    """

    def decorator(fn):
        # Derive a unique prefix for this route to prevent rate limit collisions across routes
        prefix = kwargs.get("prefix")
        if not prefix:
            prefix = f"rl:{fn.__module__}.{fn.__name__}"
            kwargs["prefix"] = prefix

        limiter = RateLimit(limit, window, **kwargs)

        @functools.wraps(fn)
        def wrapper(request, *args, **kw):
            # Middlewares expect (request, next_handler)
            return limiter(request, lambda r: fn(r, *args, **kw))

        return wrapper

    return decorator
