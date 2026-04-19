# Cache

In-memory or file-based cache. No external dependency.

## Quick start

```python
from asok import Cache

cache = Cache()  # In-memory (default)

cache.set('key', 'value')
cache.get('key')           # 'value'
cache.get('missing', 'x') # 'x' (default)
cache.forget('key')        # Delete
cache.flush()              # Clear all
```

## TTL (expiration)

```python
cache.set('token', 'abc123', ttl=300)  # Expires in 5 minutes
cache.set('session', data, ttl=3600)   # Expires in 1 hour
```

After the TTL, `get()` returns the default.

## Check existence

```python
cache.has('key')  # True/False
```

## File-based cache

Persists across server restarts:

```python
cache = Cache(backend='file', path='.cache')

cache.set('stats', {'views': 100}, ttl=600)
cache.get('stats')  # Works even after restart
```

Files are stored as JSON in the `.cache/` directory.

## Usage example — cache database queries

```python
from asok import Cache
from models.post import Post

cache = Cache()

def get_popular_posts():
    cached = cache.get('popular_posts')
    if cached:
        return cached

    posts = Post.all(order_by='-views', limit=10)
    data = [p.to_dict() for p in posts]
    cache.set('popular_posts', data, ttl=300)
    return data
```

## Usage in middleware

```python
# src/middlewares/cache.py
from asok.cache import Cache

page_cache = Cache()

def handle(request, next):
    if request.method == 'GET':
        cached = page_cache.get(request.path)
        if cached:
            return cached

    response = next(request)

    if request.method == 'GET' and request.status == '200 OK':
        page_cache.set(request.path, response, ttl=60)

    return response
```

---
[← Previous: Mail](10-mail.md) | [Documentation](README.md) | [Next: i18n →](12-i18n.md)
