# SEO & Metadata Management

Asok provides a powerful, SvelteKit-inspired system for managing page metadata (titles, meta tags, social share properties) directly from your Python logic or templates.

## 1. The `meta` object

Every `request` object has a `meta` helper that manages the page's `<head>` elements.

### Usage in Python (`page.py`)
You can set metadata attributes directly on `request.meta`.

```python
from asok import Request

def render(request: Request):
    request.meta.title = "Contact Us"
    request.meta.description = "Get in touch with the Asok team."
    return request.html("contact.html")
```

### Usage in Templates (`page.html`)
The `meta` object is available globally in templates. You can use it as a function to set values.

```html
{{ meta.title("Our Services") }}
{{ meta.description("We provide high-quality Asok development.") }}

<!-- OpenGraph and Social Media -->
{{ meta.property("og:image", static("img/hero.png")) }}
{{ meta.property("og:type", "website") }}

<!-- Custom Meta Tags -->
{{ meta.name("robots", "index, follow") }}

<!-- Canonical and Other Links -->
{{ meta.link("canonical", "https://example.com/services") }}
```

## 2. Automatic Injection & Overrides

Asok intelligently manages the injection of these tags into your final HTML.

### How it works
- **Deduplication**: If you set the same meta tag (e.g., `description`) multiple times, only the last call wins.
- **Smart Replacement**: If your base template has a hardcoded `<title>` or `<meta name="description">`, Asok will **automatically remove** them and replace them with your dynamic values. This ensures your SEO data always takes priority.
- **Escaping**: All content is automatically HTML-escaped to prevent XSS vulnerabilities.

## 3. Support for Inheritance

Metadata tags can be placed **anywhere** in your templates, including at the very top of a file that extends a base layout.

```html
{% extends "base.html" %}
{{ meta.title("This Works!") }}

{% block content %}
    <h1>Page Content</h1>
{% endblock %}
```

Asok automatically preserves these top-level side effects during the inheritance phase, ensuring they are executed even if they are outside a `{% block %}`.

## API Reference

### `meta.title(value)`
Sets the `<title>` tag. Can be assigned in Python via `request.meta.title = value`.

### `meta.description(value)`
Sets the `<meta name="description">` tag. Can be assigned in Python via `request.meta.description = value`.

### `meta.property(property, content)`
Injects a `<meta property="..." content="...">` tag (ideal for OpenGraph).

### `meta.name(name, content)`
Injects a `<meta name="..." content="...">` tag.

### `meta.link(rel, href, **attributes)`
Injects a `<link rel="..." href="...">` tag. Any additional keyword arguments are added as HTML attributes.
```python
request.meta.link("alternate", "/en", hreflang="en")
# Result: <link rel="alternate" href="/en" hreflang="en">
```

---
[← Previous: Component API](38-component-api.md) | [Documentation](README.md) | [Next: Scoped Assets →](40-scoped-assets.md)
