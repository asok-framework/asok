# Scoped JS & CSS

Asok provides an automatic scoping system for page-specific assets. This allows you to write JavaScript and CSS that only affects a single page, preventing style leakage and variable collisions without any external build tools.

## 1. Automatic Detection

Asok automatically detects and injects "companion" assets for your pages. If you have a page at `src/pages/contact/page.html` (or `contact/page.py`), Asok will automatically look for:

- `src/pages/contact/page.css`
- `src/pages/contact/page.js`

If found, these files are automatically transformed (scoped) and injected into the final HTML.

## 2. CSS Scoping

Asok uses an attribute-based scoping mechanism similar to Svelte or Vue.

### How it works
1. **Attribute Injection**: Asok automatically adds a `data-asok-page="[page-id]"` attribute to the `<body>` tag of your page.
2. **Selector Prefixing**: Every selector in your companion `.css` file is automatically prefixed to limit its reach.

**Example input (`contact/page.css`):**
```css
h1 {
    color: red;
}

.title {
    font-weight: bold;
}
```

**Example output (injected HTML):**
```css
[data-asok-page="contact"] h1 {
    color: red;
}

[data-asok-page="contact"] .title {
    font-weight: bold;
}
```

### Global Selectors (`:global`)
Sometimes you need a style to affect elements outside the page scope (like a modal appended to the document body). You can use the `:global()` wrapper to opt-out of scoping.

```css
:global(.modal-overlay) {
    background: rgba(0,0,0,0.5);
}
```

## 3. JavaScript Isolation

To prevent variables in your page-specific scripts from polluting the global `window` scope or colliding with other scripts, Asok automatically wraps your `.js` files in an **IIFE** (Immediately Invoked Function Expression).

**Example input (`contact/page.js`):**
```javascript
const name = "Contact Page";
console.log("Welcome to " + name);
```

**Example output (injected HTML):**
```javascript
(function(){
    const name = "Contact Page";
    console.log("Welcome to " + name);
})();
```

## 4. Performance: Automatic Inlining

Asok **inlines** these scoped assets directly into the HTML response instead of serving them as external files.

### Benefits
- **Zero extra HTTP requests**: The browser doesn't need to make extra round-trips to fetch page-specific assets.
- **Improved First Paint**: Styles are available immediately when the HTML is parsed.
- **Portability**: The page remains a self-contained unit of logic and style.

---
[← Previous: SEO Management](39-seo-management.md) | [Documentation](README.md) | [Next: Form Actions →](41-form-actions.md)
