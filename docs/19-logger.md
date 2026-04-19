# Logger

Request logging and application logger. No external dependency (uses Python's `logging`).

## Request logger middleware

Create `src/middlewares/logger.py`:

```python
from asok import RequestLogger

log = RequestLogger()

def handle(request, next):
    return log(request, next)
```

Output:

```
[2026-04-05 12:00:01] INFO asok.request: GET / 200 OK 3.2ms
[2026-04-05 12:00:02] INFO asok.request: POST /contact 302 Found 12.5ms
[2026-04-05 12:00:03] ERROR asok.request: GET /broken 500 8.1ms — division by zero
```

## Application logger

Use `get_logger()` anywhere in your code:

```python
from asok import get_logger

logger = get_logger("myapp")

logger.info("Server started")
logger.warning("Cache miss for key: %s", key)
logger.error("Payment failed: %s", str(e))
```

## Configuration

Via environment variables in `.env`:

```
LOG_LEVEL=INFO
LOG_FILE=app.log
```

Or directly:

```python
from asok import get_logger

logger = get_logger("myapp", level="WARNING", log_file="errors.log")
```

| Level | Description |
|---|---|
| `DEBUG` | Detailed debug info (default) |
| `INFO` | General information |
| `WARNING` | Something unexpected |
| `ERROR` | Something failed |

## Log to file

```python
# Via env
# LOG_FILE=app.log

# Or via code
logger = get_logger("myapp", log_file="app.log")
```

Logs are written to both the console and the file simultaneously.

---
[← Previous: Rate Limiting](18-rate-limit.md) | [Documentation](README.md) | [Next: API Development →](20-api.md)
