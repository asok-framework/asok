# Routing

Asok uses **file-based routing**. Your folder structure inside `src/pages/` defines your URLs. No decorators, no config.

## Basic routes

| File | URL |
|---|---|
| `src/pages/page.py` | `/` |
| `src/pages/about/page.html` | `/about` |
| `src/pages/contact/page.py` | `/contact` |
| `src/pages/blog/page.py` | `/blog` |

## Page with Python logic

Create `page.py` with a `render()` function:

```python
# src/pages/contact/page.py
from asok import Request

def render(request: Request):
    return request.html('page.html')
```

## HTTP Method handling

Instead of a single `render()` function, you can separate your logic by HTTP method. Asok will call the function matching the current method (lowercase).

The following methods are supported:
- `get(request)`
- `post(request)`
- `put(request)`
- `patch(request)`
- `delete(request)`
- `head(request)`
- `options(request)`

```python
# src/pages/contact/page.py
from asok import Request

def get(request: Request):
    # This runs for GET requests
    return request.html('page.html')

def post(request: Request):
    # This runs for POST requests
    name = request.form.get("name")
    return f"Hello {name}!"
```

If no method-specific function is found, Asok falls back to `render(request)`.

## Page with HTML only

If no logic is needed, just create `page.html`:

```html
<!-- src/pages/about/page.html -->
{% extends "html/base.html" %}

{% block main %}
<h1>About us</h1>
{% endblock %}
```

## Dynamic routes

Use brackets `[param]` in folder names to capture URL segments:

```
src/pages/blog/[slug]/page.py    → /blog/my-article
src/pages/user/[id]/page.py      → /user/42
```

Access the captured value with `request.params`:

```python
# src/pages/blog/[slug]/page.py
from asok import Request

def render(request: Request):
    slug = request.params['slug']  # "my-article"
    return request.html('page.html', slug=slug)
```

## Type-Safe Parameters

You can enforce types and automatic validation in your folder names using the `[name:type]` syntax:

```
src/pages/user/[id:int]/page.py    → Match: /user/42, No match: /user/abc
src/pages/order/[id:uuid]/page.py  → Match: /order/550e8400-e29b..., No match: /order/123
src/pages/blog/[slug:slug]/page.py → Match: /blog/hello-world, No match: /blog/Hello!
```

### Supported Types

| Type | Validation | Python Type |
|---|---|---|
| `int` | Digits only | `int` |
| `float` | Numeric (with optional dot) | `float` |
| `uuid` | Case-insensitive UUID (standard 36-char or compact 32-char) | `str` |
| `hex` | Hexadecimal characters and hyphens (1-64 chars) | `str` |
| `slug` | Lowercase letters, numbers, hyphens | `str` |
| `str` | Any non-empty string (default) | `str` |

When a type is specified, Asok validates the segment before matching. If multiple folders match (e.g., `[id:int]` and `[slug]`), Asok prioritizes the more specific typed match. Captured values in `request.params` are automatically converted to their native Python types.

## Nested dynamic routes

```
src/pages/user/[id]/posts/[post_id]/page.py → /user/42/posts/7
```

```python
def render(request: Request):
    user_id = request.params['id']      # "42"
    post_id = request.params['post_id'] # "7"
```

## Priority

When both exist, Asok checks in this order:

1. `page.py` (Python handler)
2. `page.html` (template only)

For matching: literal folders are checked before dynamic `[param]` folders.

## Static files

Files in `src/partials/` are served automatically:

| File | URL |
|---|---|
| `src/partials/css/base.css` | `/css/base.css` |
| `src/partials/js/base.js` | `/js/base.js` |
| `src/partials/images/logo.svg` | `/images/logo.svg` |

Use `static()` in templates:

```html
<link rel="stylesheet" href="{{ static('css/base.css') }}">
<img src="{{ static('images/logo.svg') }}">
```

## Custom error pages

Create a page for any HTTP error code:

- `src/pages/404/page.html`: Custom 404 page
- `src/pages/403/page.html`: Custom 403 page (triggered by CSRF failures or security protection)
- `src/pages/500/page.html`: Custom 500 page
- `src/pages/429/page.html`: Custom 429 page (Rate limiting)

```html
<!-- src/pages/404/page.html -->
{% extends "html/base.html" %}

{% block main %}
<h1>Page not found</h1>
<p>Sorry, this page doesn't exist.</p>
<a href="/">Go home</a>
{% endblock %}
```

In production (`DEBUG=false`), the 500 page is shown instead of the raw error.

---
[← Previous: Getting Started](01-getting-started.md) | [Documentation](README.md) | [Next: Request Handling →](03-request.md)
