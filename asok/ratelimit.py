from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from .request import Request


class RateLimit:
    """In-memory rate limiter middleware implementing a simple sliding window/bucket algorithm."""

    def __init__(
        self,
        max_requests: int = 60,
        window: int = 60,
        key_func: Optional[Callable[[Request], str]] = None,
        max_entries: int = 5000,
    ):
        """Initialize the rate limiter.

        Args:
            max_requests: Maximum allowed requests within the window.
            window: Time window in seconds.
            key_func: Optional function to generate a unique key for the client from the request.
            max_entries: Maximum number of tracking buckets in memory (prevents OOM).
        """
        self.max_requests = max_requests
        self.window = window
        self.key_func = key_func or self._default_key
        self.max_entries = max_entries
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._last_cleanup: float = 0.0

    @staticmethod
    def _default_key(request: Request) -> str:
        """Default key generator using the client's IP address (respects TRUSTED_PROXIES)."""
        return request.ip or "unknown"

    def _cleanup(self, now: float) -> None:
        """Remove expired buckets from the store (runs at most once per window)."""
        if now - self._last_cleanup < self.window:
            return
        self._last_cleanup = now
        expired = [k for k, v in self._store.items() if v["reset"] <= now]
        for k in expired:
            del self._store[k]

    def __call__(self, request: Request, next_handler: Callable[[Request], Any]) -> Any:
        """Middleware entry point that checks the rate limit before calling the next handler."""
        key = self.key_func(request)
        now = time.time()

        with self._lock:
            self._cleanup(now)

            if key not in self._store:
                # Eviction: if store is full, remove the oldest bucket
                if len(self._store) >= self.max_entries:
                    oldest_key = min(self._store, key=lambda k: self._store[k]["reset"])
                    del self._store[oldest_key]

                self._store[key] = {"count": 0, "reset": now + self.window}

            bucket = self._store[key]

            if bucket["reset"] <= now:
                bucket["count"] = 0
                bucket["reset"] = now + self.window

            bucket["count"] += 1

            if bucket["count"] > self.max_requests:
                remaining = int(bucket["reset"] - now)
                request.status_code(429)
                request.content_type = "text/html; charset=utf-8"
                request.environ.setdefault("asok.extra_headers", []).append(
                    ("Retry-After", str(remaining))
                )
                # Let the user override pages/429/index.html (or .py)
                app = request.environ.get("asok.app")
                if app and hasattr(app, "_render_error_page"):
                    try:
                        return app._render_error_page(request, 429)
                    except Exception:
                        pass
                return f"<h1>429 Too Many Requests</h1><p>Retry in {remaining}s.</p>"

        return next_handler(request)
