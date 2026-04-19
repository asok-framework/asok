# Tailwind CSS

Asok ships with optional, opt-in Tailwind CSS v4 support. The Tailwind standalone binary is downloaded into your project (no Node.js, no npm) and runs automatically during dev and build.

## Quick start

Scaffold a new project with Tailwind enabled:

```bash
asok create myapp --tailwind
cd myapp
asok dev
```

This downloads the Tailwind v4 standalone binary (~30 MB) into `.asok/bin/`, generates `src/static/css/base.css` with `@import "tailwindcss";`, and starts a watcher alongside the dev server.

## How it works

| Phase | What happens |
|---|---|
| `asok create --tailwind` | Downloads the binary, creates `base.css`, builds `base.build.css` |
| `asok dev` | Auto-detects Tailwind, starts a watcher subprocess that rebuilds on changes |
| `asok build` (production) | Runs a one-shot minified build |
| Templates | Reference `css/base.build.css` (the compiled output) |

The watcher and one-shot builder are detected automatically by checking for `@import "tailwindcss"` in `src/static/css/base.css`. There is no config flag — if it's there, Tailwind runs.

## Commands

### `asok tailwind --install`

(Re-)download the pinned Tailwind binary:

```bash
asok tailwind --install
#   Downloading Tailwind v4.2.2 (macos-arm64)...
#   Tailwind v4.2.2 installed
```

The binary is pinned to a specific version and stored in `.asok/bin/tailwindcss`. Re-running this only downloads if the version is missing or outdated.

### `asok tailwind --build`

One-shot build (development mode):

```bash
asok tailwind --build
```

### `asok tailwind --build --minify`

Production build:

```bash
asok tailwind --build --minify
```

## Adding Tailwind to an existing project

If you decided to add Tailwind CSS after your project was created, just run:

```bash
asok tailwind --enable
```

This command will:
1. Download the required binary.
2. Setup `src/partials/css/base.css` with the Tailwind import.
3. Update your layout to reference the compiled CSS file.
4. Perform an initial build.

## Supported platforms

The binary is downloaded from the official `tailwindlabs/tailwindcss` GitHub releases. Supported: macOS (x64/arm64), Linux (x64/arm64), Windows (x64). On unsupported platforms, install Tailwind manually and place the binary at `.asok/bin/tailwindcss`.

## Notes

- The pinned version lives in `asok/cli.py` as `TAILWIND_VERSION` (currently `4.2.2`).
- The binary is **not** committed to git — `.asok/` should be in your `.gitignore` (the scaffold does this automatically).
- The watcher logs are prefixed with `[tailwind]` so you can tell them apart from server logs.
- The admin module does **not** use Tailwind — it ships with self-contained CSS so it works regardless of whether your project uses Tailwind.

---
[← Previous: Admin Interface](26-admin.md) | [Documentation](README.md) | [Next: WebSockets →](28-websockets.md)
