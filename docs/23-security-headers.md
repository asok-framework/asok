# Security Headers

In production, Asok automatically adds security headers to every response.

## Default headers

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `X-XSS-Protection` | `1; mode=block` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Content-Security-Policy` | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'` |

## Configuration

Security headers are only applied in production (`DEBUG=false`).

### Disable entirely

```python
app.config["SECURITY_HEADERS"] = False
```

### Override specific headers

Pass a dict. Set a value to `None` to remove a header:

```python
app.config["SECURITY_HEADERS"] = {
    "Content-Security-Policy": "default-src 'self'; img-src *",
    "X-Frame-Options": None,  # removes this header
}
```

---
[← Previous: Static Versioning](22-static-versioning.md) | [Documentation](README.md) | [Next: Scheduler →](24-scheduler.md)
