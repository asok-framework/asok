# Intelligent Prefetching

Asok's Reactive Engine includes an intelligent prefetching system that makes navigation feel nearly instant. It works by background-loading HTML fragments before the user even clicks a link.

## How it works

1.  **Detection**: The engine identifies all elements with `data-block` or `data-url` that use the default `click` trigger (like links).
2.  **Trigger**: When a user hovers (`mouseover`) over such an element, Asok immediately starts fetching the associated fragment.
3.  **Caching**: The fetched HTML is stored in a client-side memory cache (`ca`).
4.  **Instant Swap**: When the user finally clicks the link, Asok checks the cache. If the fragment is already there, it performs the swap instantly without a network round-trip.

## Example

```html
<!-- Hovering over this link triggers a pre-fetch of #main from /profile -->
<a href="/profile" data-block="#main" data-push-url>View Profile</a>
```

You don't need to do anything to enable it — it's active by default for all `data-block` links.

## Optimization Details

- **Safe consuming**: Each cached fragment is consumed once. If you click again later, a new fetch will occur (unless you hover again).
- **Reduced Latency**: Prefetching typically happens during the "mouse dwell time" (the 200-400ms between hovering and clicking), which is often enough time to fetch the entire fragment.
- **Bandwidth Aware**: Only fragments (small HTML blocks) are prefetched, not full pages.
- **Header identification**: Prefetch requests send `X-Prefetch: 1` header, allowing you to optimize server-side logic if needed.

## Verification

To see it in action:
1. Open your browser's **Network tab**.
2. Hover over a link with `data-block`.
3. You will see a `fetch` request occur immediately.
4. Click the link.
5. Notice that no new request is made (or a very short one if it wasn't finished), and the page updates instantly.

> [!TIP]
> Use prefetching combined with `data-indicator` to provide immediate feedback if a pre-fetch hasn't finished yet when the user clicks.

---
[← Previous: Form Actions](41-form-actions.md) | [Documentation](README.md) | [Next: Utilities →](43-utilities.md)
