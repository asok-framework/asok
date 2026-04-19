# Utilities

Asok provides several built-in utilities to simplify common tasks in web development, all accessible via the `asok.utils` package.

## Humanize

The `asok.utils.humanize` module helps convert data into a friendly, readable format.

```python
from asok.utils import humanize

# Relative time
humanize.time_ago("2023-10-15T10:00:00Z")  # "3 days ago"

# File sizes
humanize.file_size(1048576)               # "1.0 MB"

# Thousands separators
humanize.intcomma(1500000)                # "1,500,000"

# Successive durations
humanize.duration(125)                    # "2m 5s"
```

## HTML Minification

Used automatically in production to reduce bundle sizes.

```python
from asok.utils.minify import minify_html

clean_html = minify_html("<div>  <span>  Hello  </span>  </div>")
# Result: "<div><span>Hello</span></div>"
```

## CSS & JS Scoping

These utilities are used internally by the [Asok Component system](37-reactive-components.md) but can be used manually.

### `scope_css(content, page_id)`
Prefixes all CSS selectors (except those inside `:global()`) with a specific page ID.

### `scope_js(content)`
Wraps JavaScript content in an Immediately Invoked Function Expression (IIFE) to prevent variable leakage.

## Image Optimization

Asok can optimize images by converting them to WebP.

```python
from asok.utils.image import optimize_image

# Converts source.jpg to source.jpg.webp
optimize_image("src/partials/images/source.jpg") 
```

> [!NOTE]
> Most of these utilities are already integrated into the framework's core (e.g., `request.html()` automatically minifies in production), but they remain available for manual use when building custom extensions.

---
[← Previous: Prefetching](42-prefetching.md) | [Documentation](README.md) | [Next: Transitions →](44-transitions.md)
