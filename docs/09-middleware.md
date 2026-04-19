# Middleware

Middleware lets you run code before and after every request. Create a file in `src/middlewares/` — it's loaded automatically.

## Create a middleware

```python
# src/middlewares/logger.py

def handle(request, next):
    print(f"→ {request.method} {request.path}")
    response = next(request)
    print(f"← {request.status}")
    return response
```

That's it. The file must export a `handle(request, next)` function.

- `request` — the Request object
- `next` — call this to continue to the next middleware (or the page handler)
- Return the response from `next(request)`

## Execution order

Middleware files are loaded in **alphabetical order**. To control the order, prefix filenames with numbers:

```
src/middlewares/
├── 01_logger.py      # Runs first (outermost)
├── 02_ratelimit.py   # Runs second
└── 03_auth.py        # Runs third (closest to the page)
```

## Built-in middleware

### Request Logger

```python
# src/middlewares/logger.py
from asok.logger import RequestLogger

log = RequestLogger()

def handle(request, next):
    return log(request, next)
```

Logs: `GET /about 200 OK 3.2ms`

### Rate Limiting

```python
# src/middlewares/ratelimit.py
from asok.ratelimit import RateLimit

limiter = RateLimit(max_requests=60, window=60)  # 60 requests per minute

def handle(request, next):
    return limiter(request, next)
```

Returns `429 Too Many Requests` when the limit is exceeded. Rate limiting is per IP address.

Custom key function:

```python
limiter = RateLimit(
    max_requests=100,
    window=3600,
    key_func=lambda req: req.user.id if req.is_authenticated else req.environ.get('REMOTE_ADDR')
)
```

## Examples

### Auth guard for /admin/*

```python
# src/middlewares/admin.py

def handle(request, next):
    if request.path.startswith('/admin'):
        if not request.is_authenticated:
            request.redirect('/login')
        if not request.user.is_admin:
            request.redirect('/')
    return next(request)
```

### Maintenance mode

```python
# src/middlewares/maintenance.py
import os

def handle(request, next):
    if os.environ.get('MAINTENANCE') == 'true':
        request.status_code(503)
        return '<h1>We are under maintenance. Check back soon.</h1>'
    return next(request)
```

### Response timing header

```python
# src/middlewares/timing.py
import time

def handle(request, next):
    start = time.time()
    response = next(request)
    elapsed = (time.time() - start) * 1000
    # You could log this or add a header
    return response
```

---
[← Previous: Authentication](08-authentication.md) | [Documentation](README.md) | [Next: Mail →](10-mail.md)
