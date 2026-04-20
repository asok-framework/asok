# Sessions

Server-side sessions with signed cookie IDs.

## Usage

```python
from asok import Request

def render(request: Request):
    # Read
    username = request.session.get("username")

    # Write
    request.session["username"] = "alice"
    request.session["cart"] = [1, 2, 3]

    return request.html("page.html")
```

Sessions are lazy-loaded on first access. Modified sessions are automatically saved when the response is sent.

## Configuration

| Key | Default | Description |
|---|---|---|
| `SESSION_BACKEND` | `"memory"` | `"memory"` or `"file"` |
| `SESSION_PATH` | `".sessions"` | Directory for file backend |
| `SESSION_TTL` | `86400` | Session lifetime in seconds |

### File backend

```python
app.config["SESSION_BACKEND"] = "file"
app.config["SESSION_PATH"] = ".sessions"
```

Sessions are stored as JSON files in the specified directory.

## How it works

1. A signed cookie (`asok_sid`) identifies the session
2. Session data is stored server-side (memory or file)
3. On first `request.session` access, data is loaded from the store
4. If `session.modified` is `True` at response time, data is saved back

## Session API

`request.session` behaves like a regular `dict`:

```python
request.session["key"] = "value"
request.session.get("key", "default")
del request.session["key"]
request.session.pop("key")
request.session.clear()
```

All mutating operations automatically set `session.modified = True`.

---
[← Previous: Scheduler](24-scheduler.md) | [Documentation](README.md) | [Next: Admin Interface →](26-admin.md)
