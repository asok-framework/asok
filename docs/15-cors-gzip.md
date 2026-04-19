# CORS & Gzip

## CORS (Cross-Origin Resource Sharing)

Enable CORS to allow requests from other domains (useful for APIs consumed by a frontend on a different port/domain).

### Allow all origins

```python
# wsgi.py
from asok import Asok

app = Asok()
app.config['CORS_ORIGINS'] = '*'
```

### Allow specific origins

```python
app.config['CORS_ORIGINS'] = ['http://localhost:3000', 'https://myapp.com']
```

### What it does

When enabled, Asok adds these headers to responses:

```
Access-Control-Allow-Origin: <origin>
Access-Control-Allow-Methods: GET, POST, PUT, DELETE, PATCH, OPTIONS
Access-Control-Allow-Headers: Content-Type, X-CSRF-Token, Authorization
```

Preflight `OPTIONS` requests are handled automatically with a `204 No Content` response.

### Disabled by default

```python
app.config['CORS_ORIGINS'] = None  # Default — no CORS headers
```

## Gzip Compression

Compress text responses to reduce bandwidth.

### Enable

```python
# wsgi.py
from asok import Asok

app = Asok()
app.config['GZIP'] = True
```

### How it works

- Only compresses `text/*` content types (HTML, CSS, JSON, etc.)
- Only compresses if the response is larger than `GZIP_MIN_SIZE` (default: 500 bytes)
- Only compresses if the client sends `Accept-Encoding: gzip`
- Adds `Content-Encoding: gzip` header to compressed responses

### Configure minimum size

```python
app.config['GZIP_MIN_SIZE'] = 1024  # Only compress responses > 1KB
```

### When to use

- **Development**: Leave it off (`GZIP = False`)
- **Production without reverse proxy**: Turn it on
- **Production with Nginx/Caddy**: Let the reverse proxy handle gzip instead

## ETags (Caching)

Asok automatically generates `ETag` headers for responses. This allows browsers to perform conditional requests and skip downloading content that hasn't changed.

### How it works

- Asok computes an MD5 hash of your response body.
- It sends this as an `ETag: "..."` header.
- On the next request, the browser sends `If-None-Match: "..."`.
- If the content is identical, Asok immediately returns `304 Not Modified` with an empty body.

### Configuration

ETags are enabled by default for all `200 OK` responses. You can disable them or force them in your configuration:

```python
# wsgi.py
app.config['ETAG'] = True  # Default
```

> [!TIP]
> ETags are highly efficient as they avoid sending large bodies over the network, though it still requires the server to execute your handler logic to generate the body for comparison.

---
[← Previous: CLI](14-cli.md) | [Documentation](README.md) | [Next: Deployment →](16-deployment.md)
