# Scheduled Tasks

Run functions at regular intervals using daemon threads.

## Usage

```python
from asok import schedule

def cleanup():
    print("Cleaning up old sessions...")

task = schedule(3600, cleanup)  # every hour
```

The first execution happens after the interval (not immediately).

### With arguments

```python
schedule(60, send_ping, "https://example.com", timeout=5)
```

### Cancel a task

```python
task.cancel()
print(task.is_cancelled)  # True
```

Cancellation is immediate — uses `threading.Event.wait()` internally.

## Error handling

Exceptions in scheduled tasks are logged (via `logging.exception`) but do not stop the scheduler. The task continues running on the next interval.

## Notes

- Tasks run in daemon threads (they stop when the main process exits)
- Each `schedule()` call creates one thread
- Suitable for lightweight recurring work (cleanup, pings, cache refresh)

---
[← Previous: Security Headers](23-security-headers.md) | [Documentation](README.md) | [Next: Sessions →](25-sessions.md)
