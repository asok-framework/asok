# HTML Streaming

Asok provides native support for HTML streaming (Chunked Transfer Encoding). This allows you to serve HTML in chunks as they are generated, rather than waiting for the entire page to be rendered.

## Usage

Use `request.stream()` instead of `request.html()` to initiate a streaming response.

```python
from asok import Request

def render(request: Request):
    return request.stream('page.html', title="Streaming Demo")
```

## How it Works

1.  **Generator Support**: When `request.stream()` is called, the template engine returns a Python generator that yields content chunks.
2.  **SmartStreamer**: Asok wraps this generator in a `SmartStreamer` instance.
3.  **Automatic Injection**: The first chunk delivered to the browser automatically includes:
    *   CSRF meta tags.
    *   Core reactive engine scripts (for block swapping and WebSockets).
    *   Live reload scripts (in DEBUG mode).
4.  **Compression**: If `GZIP=true` is set in your config and the browser supports it, the stream is compressed on-the-fly.

## Benefits

*   **Faster Perceived Performance (TTFB)**: The browser receives the `<head>` and initial styles immediately, allowing it to start downloading assets while the server is still generating the rest of the page.
*   **Low Memory Footprint**: Large templates are never fully held in memory; chunks are sent and discarded as generated.
*   **Interactive First Chunk**: Security tokens and utility scripts are guaranteed to arrive before any template content, preventing race conditions in the frontend.

## Configuration

Streaming respects the global `GZIP` setting:

```python
# .env or app.config
GZIP=true
```

> [!NOTE]
> `request.stream()` automatically detects `X-Block` headers (used for partial updates). In such cases, it falls back to standard rendering to ensure the partial fragment is delivered correctly without full page headers.

---
[← Previous: Security Audit](30-security-audit.md) | [Documentation](README.md) | [Next: Advanced Authentication →](32-authentication-advanced.md)
