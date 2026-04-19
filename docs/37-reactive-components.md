# Reactive Components

Reactive (Live) Components allow you to build interactive UI elements that update in real-time without manual JavaScript. They are isomorphic: rendered on the server as HTML, but reactive via WebSockets.

## Key Features

*   **Zero JavaScript**: Write your logic in Python; Asok handles the WebSocket synchronization.
*   **Stateful**: Components maintain their internal properties (`self.count`, etc.) across interactions.
*   **Persistent Sessions**: Components can leverage `self.session` to persist state across page reloads.
*   **Isomorphic**: Initial render is SEO-friendly HTML; subsequent updates send only necessary HTML fragments.
*   **Alive Engine**: Powered by the "Alive" JS runtime, providing smart focus preservation and automatic CSRF synchronization.

## Basic Example

### 1. The Component (`src/components/Counter.py`)


```python
# src/components/Counter.py
from asok import Component
from asok.component import exposed

class Counter(Component):
    count = 0

    @exposed
    def increment(self):
        self.count += 1

    def render(self):
        return self.html("counter.html")
```

### 2. The Template (`src/components/counter.html`)

```html
<div>
    <h3>Count: {{ count }}</h3>
    <button ws-click="increment">Add 1</button>
</div>
```

### 3. Usage in a Page

```html
{% extends "html/base.html" %}
{% block main %}
    <h1>Welcome</h1>
    {{ component('Counter', count=10) }}
{% endblock %}
```

## Exposing Methods

For security reasons, component methods must be explicitly marked with the `@exposed` decorator to be callable from the frontend via WebSocket.

```python
from asok import Component
from asok.component import exposed

class Counter(Component):
    count = 0

    @exposed
    def increment(self):
        self.count += 1

    @exposed
    def decrement(self):
        self.count -= 1

    # This method is NOT exposed and cannot be called from the frontend
    def _internal_calculation(self):
        return self.count * 2
```

> [!IMPORTANT]
> Only methods decorated with `@exposed` can be triggered via `ws-click`, `ws-input`, or `ws-submit` directives. This prevents unauthorized access to internal component methods.

## How it Works

1.  **Initial Render**: The `{{ component(...) }}` helper renders the component on the server and embeds a signed version of its state in a `data-asok-state` attribute.
2.  **Connection**: The browser's reactive engine connects to the WebSocket server (`/asok/live`).
3.  **Synchronization**: When a `ws-click` or other trigger is activated:
    *   The browser sends the component's signed state and the method name to the server.
    *   The server reconstructs the component, validates the state hash, and executes the method.
    *   The component is re-rendered on the server.
    *   The server sends the new HTML back to the browser.
    *   The browser performs an efficient DOM swap and **preserves focus/cursor position** automatically.

## The "Alive" Reactive Engine

Asok includes the **Alive** engine, a lightweight (< 2KB) JavaScript runtime that handles the bridge between your DOM and the server. It handles:

-   **Automatic Connectivity**: Reconnects WebSockets automatically if the connection is lost.
-   **Security**: Synchronizes signed state and CSRF tokens for every interaction.
-   **UX Polish**: Restores input focus and text selection after a component update, preventing "jumping" inputs during fast typing.

## Automatic State Persistence
 
 Asok components are designed to feel like SPA components. On every interaction (e.g., clicking a `ws-click` button), the framework automatically persists the component's state to the user's session store.
 
 ### Benefits
 - **Refresh Protection**: If the user reloads the page, the component restores its exact state from the session.
 - **Navigation Stability**: Navigating between pages or using the browser's back/forward buttons preserves the state of components.
 - **Development Stability**: In development mode (`DEBUG=true`), Asok uses a deterministic development key to ensure state survives server restarts and hot-reloads.
 
 ## Persistent Sessions
 
 Components have a `self.session` property that behaves like `request.session`. This is useful for explicit data that must be shared across the entire application.
 

 ```python
 def increment(self):
     self.session["pcount"] = self.session.get("pcount", 0) + 1
     self.session.modified = True  # Required to persist changes back to the store
 ```
 

 > [!IMPORTANT]
 > Always set `self.session.modified = True` when updating session data within a component method to ensure the changes are saved to the persistent store.

---
[← Previous: Vector Search](36-vector-search.md) | [Documentation](README.md) | [Next: Component API →](38-component-api.md)
