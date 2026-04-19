# Rate Limit

In-memory rate limiter. No external dependency.

## Quick start

Create `src/middlewares/ratelimit.py`:

```python
from asok import RateLimit

limiter = RateLimit(max_requests=60, window=60)  # 60 req/min

def handle(request, next):
    return limiter(request, next)
```

When a client exceeds the limit, they receive a `429 Too Many Requests` response with the remaining wait time.

## Options

```python
RateLimit(
    max_requests=60,  # Max requests per window (default: 60)
    window=60,         # Window duration in seconds (default: 60)
    key_func=None,     # Custom function to identify clients
)
```

## Custom key function

By default, clients are identified by their IP address (`X-Forwarded-For` or `REMOTE_ADDR`). You can provide a custom key function:

```python
# Rate limit per user instead of per IP
def by_user(request):
    if request.is_authenticated:
        return f"user:{request.user.id}"
    return request.environ.get("REMOTE_ADDR", "unknown")

limiter = RateLimit(max_requests=100, window=60, key_func=by_user)
```

## Different limits per route

```python
from asok import RateLimit

api_limiter = RateLimit(max_requests=30, window=60)
login_limiter = RateLimit(max_requests=5, window=300)  # 5 attempts / 5 min

def handle(request, next):
    if request.path.startswith("/api/"):
        return api_limiter(request, next)
    if request.path == "/login" and request.method == "POST":
        return login_limiter(request, next)
    return next(request)
```

---
[← Previous: Background Tasks](17-background.md) | [Documentation](README.md) | [Next: Request Logger →](19-logger.md)
