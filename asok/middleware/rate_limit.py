"""Global rate limiting middleware.

SECURITY: Prevents DoS attacks, API abuse, and brute force attempts.
"""

import time
from typing import Any, Callable

from asok.cache import default_cache

_EXEMPT_PREFIXES = (
    "/__health",
    "/__reload",
    "/static/",
    "/css/",
    "/js/",
    "/images/",
    "/uploads/",
)


def _is_exempt_path(path: str) -> bool:
    return path.startswith(_EXEMPT_PREFIXES)


def _should_skip_rate_limit(request: Any, app: Any) -> bool:
    if not app or not app.config.get("RATE_LIMIT", True):
        return True
    return _is_exempt_path(request.path)


def rate_limit_middleware(request: Any, next_handler: Callable) -> Any:
    """Apply global rate limiting per IP address.

    Default: 100 requests per minute per IP.
    Configurable via RATE_LIMIT_PER_MINUTE in config.

    Args:
        request: The request object
        next_handler: Next middleware/handler in chain

    Returns:
        Response from next handler or 429 Too Many Requests
    """
    # Skip if already enforced earlier in the request lifecycle
    if getattr(request, "_rate_limit_checked", False):
        return next_handler(request)
    request._rate_limit_checked = True

    app = request.environ.get("asok.app")
    if _should_skip_rate_limit(request, app):
        return next_handler(request)

    # Get rate limit from config (default: 100 req/min)
    rate_limit = app.config.get("RATE_LIMIT_PER_MINUTE", 100)

    # Rate limit key: IP + current minute
    current_minute = int(time.time() // 60)
    key = f"rate_limit:{request.ip}:{current_minute}"

    # Atomic increment operation (cross-process safe when using Redis cache)
    # to prevent TOCTOU race conditions.
    count = default_cache.incr(key, ttl=60)
    if count > rate_limit:
        request.status_code(429)
        return request.text(
            f"Too many requests. Limit: {rate_limit} requests per minute. "
            "Please try again later.",
            status=429,
        )

    return next_handler(request)
