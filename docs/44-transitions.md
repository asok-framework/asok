# Transitions

Asok provides a minimalist, Svelte-inspired transition system to add fluid animations to your UI when content is updated via SPA or WebSockets.

## Overview

Transitions in Asok are **independent** and **opt-in**. The JavaScript and CSS required for animations are only injected into the page if the `asok-transition` attribute is detected in your template.

## Usage

To animate an element during a swap, add the `asok-transition` attribute to it.

```html
<div id="content" asok-transition="fade">
    Initial Content
</div>

<button data-url="/update" data-block="#content">
    Update
</button>
```

### Supported Types

Asok comes with several built-in transition types:

| Type | Description |
| :--- | :--- |
| `fade` | Smooth opacity transition (default). |
| `slide` | Horizontal slide and fade effect. |
| `scale` | Subtle scale up/down with fade. |

### Customizing Duration

You can specify the duration (in milliseconds) as a second parameter in the attribute:

```html
<!-- Slow fade -->
<div asok-transition="fade 600">...</div>

<!-- Fast slide -->
<div asok-transition="slide 150">...</div>
```

## How it Works

When a swap is triggered (via `data-block`, `ws-*`, or `data-sse`), the framework:
1. Detects if the target element has an `asok-transition` attribute.
2. Applies a `leaving` class to the old content.
3. Performs the actual DOM swap after the "out" animation finishes.
4. Applies an `entering` class to the new content to trigger the "in" animation.

## WebSocket Support

Transitions work automatically with **Alive Components**. If your component root or any internal block has the attribute, updates pushed via WebSocket will be animated.

```python
# In a component or page
def render(request):
    return f"""
    <div id="live-clock" asok-transition="fade">
        {datetime.now()}
    </div>
    """
```

## CSS Customization

The framework injects standard CSS classes. You can override them in your own stylesheets for custom effects:

- `.asok-fade-out`, `.asok-fade-in`
- `.asok-slide-out`, `.asok-slide-in`
- `.asok-scale-out`, `.asok-scale-in`
- `.is-leaving`, `.is-entering`

---
[← Previous: Utilities](43-utilities.md) | [Documentation](README.md)
