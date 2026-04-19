[← Back to Repository](../README.md)

# Asok Documentation

A minimalist, zero-dependency Python web framework. Simpler than Flask.

| # | Topic | Description |
|---|---|---|
| 01 | [Getting Started](01-getting-started.md) | Installation, project structure, first steps |
| 02 | [Routing](02-routing.md) | File-based routing, dynamic params, error pages |
| 03 | [Request](03-request.md) | Input, output, redirect, flash, CSRF, file download |
| 04 | [Templates](04-templates.md) | Variables, filters, loops, inheritance, includes |
| 05 | [ORM](05-orm.md) | Models, CRUD, relations, pagination, passwords, slugs |
| 06 | [Forms](06-forms.md) | Declarative forms, field types, custom classes |
| 07 | [Validation](07-validation.md) | 14 rules, custom messages, file validation |
| 08 | [Authentication](08-authentication.md) | Login, logout, sessions, protecting pages |
| 09 | [Middleware](09-middleware.md) | Request/response pipeline, rate limiting, logging |
| 10 | [Mail](10-mail.md) | Send emails via SMTP |
| 11 | [Cache](11-cache.md) | In-memory and file-based caching |
| 12 | [i18n](12-i18n.md) | Multi-language support with JSON locales |
| 13 | [Testing](13-testing.md) | WSGI test client, assertions |
| 14 | [CLI](14-cli.md) | Generators, migrations, seeder, dev server |
| 15 | [CORS & Gzip](15-cors-gzip.md) | Cross-origin requests, response compression |
| 16 | [Deployment](16-deployment.md) | Gunicorn, Nginx, systemd, production config |
| 17 | [Background Tasks](17-background.md) | Run functions in background threads |
| 18 | [Rate Limit](18-rate-limit.md) | Per-IP / per-route request throttling |
| 19 | [Logger](19-logger.md) | Request logging middleware |
| 20 | [API Helpers](20-api.md) | JSON API responses and errors |
| 21 | [File Storage](21-file-storage.md) | Uploaded file handling and saving |
| 22 | [Static Versioning](22-static-versioning.md) | Cache-busted static asset URLs |
| 23 | [Security Headers](23-security-headers.md) | CSP, X-Frame-Options, etc. |
| 24 | [Scheduler](24-scheduler.md) | Recurring scheduled tasks |
| 25 | [Sessions](25-sessions.md) | Server-side sessions (memory or file) |
| 26 | [Admin](26-admin.md) | Auto-generated Django-style admin interface |
| 27 | [Tailwind](27-tailwind.md) | Optional Tailwind CSS v4 integration (zero npm) |
| 28 | [WebSockets](28-websockets.md) | Standard-library WebSocket server with automatic auth |
| 29 | [Serialization](29-serialization.md) | Model to JSON serialization via Schema |
| 30 | [Optimization](30-optimization.md) | Performance tuning and caching strategies |
| 30 | [Security Audit](30-security-audit.md) | 2026 security audit results and hardening measures |
| 31 | [HTML Streaming](31-html-streaming.md) | Chunked delivery, SmartStreamer, and generator responses |
| 32 | [Advanced Authentication](32-authentication-advanced.md) | JWT, OAuth2, magic links |
| 33 | [Advanced Deployment](33-deployment.md) | Docker, CI/CD, zero-downtime deploys |
| 34 | [Advanced ORM](34-orm-advanced.md) | Raw SQL, transactions, advanced relations |
| 35 | [Advanced Forms](35-forms-advanced.md) | Dynamic forms, file uploads, model forms |
| 36 | [Vector Search](36-vector-search.md) | Cosine/Euclidean similarity search in SQLite |
| 37 | [Reactive Components](37-reactive-components.md) | Server-side reactive UI with component synchronization |
| 38 | [Component API](38-component-api.md) | Detailed reference for Component methods and lifecycle |
| 39 | [SEO Management](39-seo-management.md) | Page titles, meta tags, and head inheritance |
| 40 | [Scoped Assets](40-scoped-assets.md) | Page-specific scoped CSS and JS isolation |
| 41 | [Form Actions](41-form-actions.md) | Native data mutation with action dispatcher |
| 42 | [Prefetching](42-prefetching.md) | Instant navigation with intelligent background loading |
| 43 | [Utilities](43-utilities.md) | Built-in helper functions and utilities |
| 44 | [Transitions](44-transitions.md) | Svelte-style animations for SPA and WebSockets |

## Why Asok?

| | Flask | Asok |
|---|---|---|
| Routing | Decorators (`@app.route`) | Folder structure (automatic) |
| Database | Install SQLAlchemy | Built-in SQLite ORM |
| Forms | Install WTForms | Built-in `Form` class |
| Auth | Install Flask-Login | Built-in `request.login()` |
| Templates | Jinja2 (dependency) | Built-in engine (same syntax) |
| i18n | Install Flask-Babel | Built-in JSON locales |
| Mail | Install Flask-Mail | Built-in `Mail.send()` |
| CSRF | Install Flask-WTF | Built-in (automatic) |
| Dependencies | 5+ packages | **Zero** |
| Performance | Varies | Cached routes, templates, static, SQLite WAL |
| Port conflict | Manual | Auto-finds free port |

## Performance

In production (`DEBUG=false`), Asok caches everything automatically:

- **Routes** — file-system walk cached per URL
- **Modules** — page `.py` files loaded once
- **Templates** — compiled to Python functions, cached by content hash
- **Static files** — served from memory with `Cache-Control` headers
- **Middleware** — chain built once at startup
- **SQLite** — thread-local connections + WAL mode for concurrent reads
- **Regex** — all patterns pre-compiled at import time

No configuration. Just `DEBUG=false`.

## Architecture

```
asok/
├── core.py        # App, routing, WSGI, CORS, gzip
├── request.py     # Request/response, auth, flash, i18n
├── orm.py         # SQLite ORM, models, relations
├── templates.py   # Template engine
├── forms.py       # Form builder
├── validation.py  # Validation rules
├── mail.py        # Email sending
├── cache.py       # Cache (memory/file)
├── logger.py      # Request logging
├── ratelimit.py   # Rate limiting
├── testing.py     # Test client
├── utils/         # Helpers (minify, image, humanize)
└── cli.py         # CLI commands
```

Total: ~3000 lines of Python. Zero external dependencies.
