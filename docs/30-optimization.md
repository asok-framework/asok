# Optimization

Asok features a built-in optimization engine designed to make your application production-ready with zero external dependencies.

## 1. HTML Minification (Standard Library)

In production mode, Asok automatically minifies your HTML output to reduce page weight and improve loading times.

### How it works
- **Zero Dependencies**: Uses Python's standard `re` module.
- **Safety**: Automatically protects content inside `<pre>`, `<code>`, `<script>`, `<style>`, and `<textarea>` tags.
- **Smart Cleaning**: Removes HTML comments and collapses unnecessary whitespace between tags.

### Configuration
By default, minification is enabled when `DEBUG=false`. You can manually control it in `.env`:

```env
HTML_MINIFY=true
```

## 2. Image Optimization (WebP)

Asok can automatically convert your images to the modern **WebP** format.

### How it works
- **On-the-fly serving**: The `static()` helper automatically redirects to the `.webp` version if it exists.
- **Auto-Conversion**: Images uploaded via the Admin are automatically optimized.
- **Standalone Binary**: Uses Google's `cwebp` binary (downloaded into `.asok/bin/`).

### Quick Start
Enable it during project creation:
```bash
asok create myapp --image
```

Or add it later:
```bash
asok image --enable
```

### Full Scan
Optimize all existing images in your project:
```bash
asok image --optimize
```

To clean up disk space by removing original files after conversion:
```bash
asok image --optimize --delete-originals
```

### Automatic File cleanup
In production, you can tell Asok to delete original assets right after they are converted during upload:
```env
IMAGE_KEEP_ORIGINAL=false
```

## 3. JS & CSS Minification (Esbuild)

Asok uses the official standalone **Esbuild** binary to provide ultra-fast minification for your JavaScript and CSS files located in `src/partials/`.

### How it works
- **Performance**: Esbuild is written in Go and is significantly faster than Node-based tools.
- **Auto-serving**: In production (`DEBUG=false`), the `static()` helper automatically redirects to the `.min.js` or `.min.css` version if it exists.
- **Standalone Binary**: Downloads into `.asok/bin/` with no Node.js required.

### Quick Start
Install the binary:
```bash
asok assets --install
```

### Manual Minification
```bash
asok assets --minify
```

### Automatic Build
The `asok preview` command automatically runs a minification pass before starting the production server, ensuring all your assets are optimized.

## 4. Tailwind CSS Minification

If you use Tailwind CSS, Asok uses the official standalone binary to minify your CSS during production builds.

```bash
asok preview
#   Building CSS (minified)...
```

This runs a one-shot minified build of your `src/partials/css/base.css` into `base.build.css`.

## 5. Gzip Compression

Asok includes a built-in Gzip middleware to compress HTML, CSS, JS, and JSON responses.

### Configuration
```env
GZIP=true
GZIP_MIN_SIZE=500  # Minimum size in bytes to trigger compression
```

## 6. Smart Conditional Asset Injection

Asok automatically reduces the JavaScript payload of your pages by only loading the reactive engines when they are actually needed.

### How it works
- **Scan**: During the final rendering phase, Asok performs a lightning-fast scan of your HTML content.
- **Triggers**: It looks for specific attributes like `data-block`, `data-sse`, `ws-*`, or `data-asok-component`.
- **Injection**: 
  - The **Reactive Engine** is only injected if `data-*` attributes are found.
  - The **Alive Engine** (WebSockets) is only injected if `ws-*` or component attributes are found.
- **Result**: Static pages remain purely static with zero JavaScript overhead, while reactive pages maintain full functionality automatically.

## 7. Scoped Assets (Page-specific Optimization)

Asok allows you to reduce your global CSS and JS payload by isolating page-specific logic and styles.

### How it works
- **Granular Loading**: Assets are only loaded for the specific page that needs them.
- **Inlining**: Scoped assets are inlined directly into the HTML response, reducing the number of HTTP requests and improving Time to First Meaningful Paint.
- **Isolation**: Prevents side-effects and style leakage, ensuring that optimizations on one page don't break others.

See the [Scoped Assets Guide](40-scoped-assets.md) for detailed usage.

## 8. Clean Project Structure (Bytecode/pycache)

Because Asok uses a file-system based routing system, your project contains many directories and Python files. To keep your workspace clean, Asok automatically disables the generation of `__pycache__` folders by default.

### How it works
- **Automatic Cleanup**: Asok set `sys.dont_write_bytecode = True` globally as soon as the framework is imported.
- **Cleaner Workspace**: Your routing folders remain free of `.pyc` files and `__pycache__` directories.
- **Production Efficiency**: While bytecode execution is slightly faster, the overhead of modern Python and the I/O of SSDs makes this cleanup preferred for a better developer experience.

### Configuration
If you specifically need to enable bytecode generation (e.g. for mission-critical performance in production), set the following environment variable:

```bash
ASOK_WRITE_BYTECODE=true
```

## Deployment (Production)

Don't forget to install the required binaries on your production server:

```bash
asok image --install
asok tailwind --install
```

---
[← Previous: Model Serialization](29-serialization.md) | [Documentation](README.md) | [Next: Security Audit →](30-security-audit.md)
