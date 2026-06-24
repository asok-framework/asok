<p align="center">
  <img src="https://raw.githubusercontent.com/asok-framework/asok/main/icons/logo.svg" alt="Asok Framework Logo" width="400" />
</p>

<p align="center">
  <a href="https://github.com/asok-framework/asok/stargazers"><img src="https://img.shields.io/github/stars/asok-framework/asok?style=for-the-badge&color=ffd700" alt="GitHub Stars"></a>
  <a href="https://github.com/asok-framework/asok/blob/main/LICENSE"><img src="https://img.shields.io/github/license/asok-framework/asok?style=for-the-badge&color=4169e1" alt="License"></a>
  <a href="https://pypi.org/project/asok/"><img src="https://img.shields.io/pypi/v/asok?style=for-the-badge&color=228b22" alt="PyPI Version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="https://github.com/asok-framework/asok/actions"><img src="https://img.shields.io/github/actions/workflow/status/asok-framework/asok/tests.yml?style=for-the-badge&label=tests" alt="Tests"></a>
</p>

<p align="center"><strong>Full-stack Python. Zero runtime dependencies.</strong></p>

---

Asok is a batteries-included Python web framework built entirely on the standard library. It gives you routing, ORM, templates, admin interface, REST and GraphQL APIs, WebSockets, background tasks, and SSG/ISR — all from a single `pip install`, with nothing else required at runtime.

Built for developers who want a complete stack without assembling one.

🌐 **[Documentation](https://asok-framework.com/docs)** · 💬 **[Discord](https://discord.com/invite/aYYkuPT3qR)** · 🎥 **[Tutorials](https://www.youtube.com/@asok-framework)**

---

## When to choose Asok

<table style="width: 100%; table-layout: fixed;">
  <thead>
    <tr style="text-align: left;">
      <th style="width: 40%;"></th>
      <th style="width: 20%;">Flask</th>
      <th style="width: 20%;">Django</th>
      <th style="width: 20%;">Asok</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Runtime dependencies</td>
      <td>~6</td>
      <td>~20+ transitive</td>
      <td><strong>0</strong></td>
    </tr>
    <tr>
      <td>ORM built-in</td>
      <td>✗</td>
      <td>✓</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>Admin interface</td>
      <td>✗</td>
      <td>✓</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>File-based routing</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>GraphQL built-in</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>WebSockets built-in</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>Reactive components</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>SSG / ISR</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>Auto OpenAPI docs</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
    <tr>
      <td>Background tasks</td>
      <td>✗</td>
      <td>✗</td>
      <td>✓</td>
    </tr>
  </tbody>
</table>

**Choose Asok when** you want a full stack out of the box, dependency auditability matters (security-critical environments, embedded deployments, strict supply chain policies), or you're a solo developer or small team who doesn't want to assemble and maintain a stack of integrations.

---

## A complete app in one file

```python
# wsgi.py
from asok import Asok, Field, Model, Admin

app = Asok(__name__)

class Post(Model):
    title   = Field.String(nullable=False)
    body    = Field.Text()
    author  = Field.String()

admin = Admin(app)
```

```python
# src/pages/page.py
from asok import Request
from models.post import Post

def render(request: Request):
    posts = Post.query().order_by("-id").limit(10).get()
    return request.render("page.html", posts=posts)
```

That's a working app with database, admin interface, and a paginated index page. Run it:

```bash
pip install asok
asok create my-blog && cd my-blog
asok migrate
asok dev
```

---

## Live Interactivity & Reactivity (No Client JS Needed)

Asok provides two built-in options for building interactive frontends, both operating on **Zero-Eval Security** (strict CSP compliance, no `'unsafe-eval'` required).

### 1. Live Stateful Components (Real-time WebSockets)
Create reactive, server-side components that synchronize state automatically over WebSockets using the `@exposed` decorator.

```python
# src/components/counter.py
from asok import Component
from asok.component import exposed

class Counter(Component):
    """Reusable UI component for Counter."""
    count = 0

    @exposed
    def increment(self):
        self.count += 1

    def render(self):
        return self.html("counter.html")
```

```html
<!-- src/components/counter.html -->
<div>
    <h3>Count: {{ count }}</h3>
    <button ws-click="increment">Add 1</button>
</div>
```

```html
<!-- In any page template (e.g., src/pages/page.html) -->
{{ component('Counter', count=10) }}
```

### 2. Client-Side Reactive Directives
For offline or local state updates, use native lightweight reactive directives directly in your HTML markup (~5KB client runtime, zero build step):

```html
<div asok-state="{ count: 0 }">
  <h3>Count: <span asok-text="count"></span></h3>
  <button asok-on:click="count++">Add 1</button>
</div>
```

---

## Features

### Routing & Templates
- **File-based routing** — `src/pages/blog/[slug]/page.py` maps to `/blog/hello-world`
- **Dynamic parameters** — `[id]`, `[slug:slug]`, catch-all patterns
- **Template engine** — Jinja-compatible with inheritance, macros, and auto-escaping
- **HTML streaming** — chunked responses for instant TTFB

### ORM
- **Multi-database** — SQLite (default), PostgreSQL, MySQL with connection pooling
- **Relations** — HasMany, BelongsTo, BelongsToMany, MorphTo, self-referencing
- **Migrations** — automatic schema diffing, rollback, multi-DB
- **Security** — parameterized queries, column whitelisting, mass-assignment protection, encrypted fields (Fernet AES-256)
- **Password fields** — PBKDF2-SHA256 with 600,000 iterations

### API
- **REST** — decorator-based routes with automatic OpenAPI 3.0 generation and live Swagger UI
- **GraphQL** — schema auto-generated from ORM models, playground in development, WS subscriptions
- **API versioning** — URL-based and header-based, deprecation sunset headers
- **Bearer token auth** — HMAC-signed, configurable expiry

### Real-time
- **WebSockets** — rooms, presence tracking, typing indicators, direct messages
- **Live components** — server-driven reactive UI over WebSockets
- **Client reactivity** — `asok-state`, `asok-on:click`, `asok-text` directives (~3KB, no build step)

### Admin interface
- Auto-generated CRUD for every model
- Role-based access control (RBAC)
- Two-factor authentication (TOTP + backup codes)
- Audit logs, inline editing, advanced filters
- Fully customizable templates

### Infrastructure
- **WSGI + ASGI** — run on Gunicorn or Uvicorn
- **Background tasks** — thread pool (local) or Redis queue (`asok worker`) with HMAC-signed job envelopes
- **Caching** — in-memory, Redis, fragment caching
- **Sessions** — HMAC-signed, Redis-backed, HttpOnly + SameSite=Strict
- **Static site generation** — SSG for static routes, ISR with background cache warming
- **Islands architecture** — selective hydration for performance-critical pages
- **Email** — SMTP with templates, async dispatch via Redis
- **S3 storage** — AWS S3 integration with automatic mime-type detection

### Security (audited)
- CSRF protection with auto-rotation and HMAC validation
- Content Security Policy with per-request nonces
- HSTS, X-Frame-Options, X-Content-Type-Options, Permissions-Policy
- HTML and SVG sanitizer (two-pass whitelist)
- Path traversal prevention on file uploads
- SQL injection protection (parameterized queries + identifier validation)
- Rate limiting (per-IP, per-user, configurable windows)
- GraphQL mutations blocked by default without `GRAPHQL_AUTHORIZE`

### Developer experience
- **CLI** — `asok create`, `asok dev`, `asok migrate`, `asok make model`, `asok build`
- **Production build** — bytecode compilation, JS/CSS minification, WebP conversion
- **Testing** — built-in test client, `TestClient`, fixture helpers
- **Developer toolbar** — request inspector, query analyzer, cache stats in-browser
- **i18n** — `{{ __('key') }}` with JSON locale files, translation management UI
- **Extensions** — community plugin system with secure path sandboxing
- **VSCode extension** — syntax highlighting, IntelliSense, route navigation

---

## Installation

```bash
pip install asok
```

Asok has zero runtime dependencies. SQLite works out of the box. Add extras only if you need them:

```bash
pip install "asok[postgres]"        # PostgreSQL
pip install "asok[mysql]"           # MySQL
pip install "asok[redis]"           # Redis (caching, sessions, background tasks)
pip install "asok[async]"           # ASGI / async support
pip install "asok[postgres,redis]"  # Combined
```

---

## Quick start

```bash
asok create my-project
cd my-project
asok dev
```

Open [http://localhost:8000](http://localhost:8000). Edit `src/pages/page.html` to start.

---

## Project structure

```
my-project/
├── src/
│   ├── components/       # Reactive components
│   ├── locales/          # Translations (en.json, fr.json, ...)
│   ├── middlewares/      # Request interceptors
│   ├── models/           # ORM models
│   ├── pages/            # Routes (page.py + page.html)
│   └── partials/         # css, js, images, uploads
└── wsgi.py               # Entry point
```

---

## Production

```bash
# WSGI
gunicorn wsgi:app

# ASGI
uvicorn asgi:app
```

Required environment variables:

```env
DEBUG=false
SECRET_KEY=your-64-character-key   # generate: python -c "import secrets; print(secrets.token_hex(32))"
APP_URL=https://yourdomain.com
DATABASE_URL=sqlite:///data/prod.db
```

Generate a deployment config:

```bash
asok deploy   # outputs Gunicorn + Nginx + SystemD configs
asok build    # optimized production build (bytecode + minification)
```

---

## Roadmap

| Version | Status | Focus |
|---|---|---|
| v0.4.0 | ✅ Released (June 2026) | GraphQL, extensions, SSG/ISR, advanced WebSockets |
| v0.5.0 | ✅ Released (June 2026) | Security hardening, GraphQL auth, signed Redis jobs, offline GraphiQL |
| v0.5.1 | ✅ Released (June 2026) | CLI and database connection patch updates |
| v1.0.0 | 📋 Q3 2026 | Stable API, monitoring, multi-tenancy, CDN pipeline |

Full details in [ROADMAP.md](ROADMAP.md).

---

## Contributing

```bash
git clone https://github.com/asok-framework/asok.git
cd asok
python -m venv venv && source venv/bin/activate
pip install -e .
python -m pytest
```

- [Report a bug](https://github.com/asok-framework/asok/issues/new?template=bug_report.md)
- [Suggest a feature](https://github.com/asok-framework/asok/discussions)
- [Read the contributing guide](CONTRIBUTING.md)
- [Join Discord](https://discord.com/invite/aYYkuPT3qR)

<a href="https://github.com/asok-framework/asok/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=asok-framework/asok" />
</a>

---

## License

MIT — see [LICENSE](LICENSE).
