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
    # Get app instance from request environ
    app = request.environ.get("asok.app")
    if not app:
        return next_handler(request)

    # Check if rate limiting is enabled
    rate_limit_enabled = app.config.get("RATE_LIMIT", True)
    if not rate_limit_enabled:
        return next_handler(request)

    # Exempt certain paths from rate limiting
    if _is_exempt_path(request.path):
        return next_handler(request)

    # Get rate limit from config (default: 100 req/min)
    rate_limit = app.config.get("RATE_LIMIT_PER_MINUTE", 100)

    # Rate limit key: IP + current minute
    current_minute = int(time.time() // 60)
    key = f"rate_limit:{request.ip}:{current_minute}"

    # Get current count
    count = default_cache.get(key, 0)

    if count >= rate_limit:
        # Rate limit exceeded
        request.status_code(429)
        return request.text(
            f"Too many requests. Limit: {rate_limit} requests per minute. "
            "Please try again later.",
            status=429,
        )

    # Increment counter with 60s TTL
    default_cache.set(key, count + 1, ttl=60)

    # Process request
    return next_handler(request)
