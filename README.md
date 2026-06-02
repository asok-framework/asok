<p align="center">
  <img src="https://raw.githubusercontent.com/asok-framework/asok/main/icons/logo.svg" alt="Asok Framework Logo" width="400" />
</p>

<p align="center">
  <a href="https://github.com/asok-framework/asok/stargazers"><img src="https://img.shields.io/github/stars/asok-framework/asok?style=for-the-badge&color=ffd700" alt="GitHub Stars"></a>
  <a href="https://github.com/asok-framework/asok/blob/main/LICENSE"><img src="https://img.shields.io/github/license/asok-framework/asok?style=for-the-badge&color=4169e1" alt="License"></a>
  <a href="https://pypi.org/project/asok/"><img src="https://img.shields.io/pypi/v/asok?style=for-the-badge&color=228b22" alt="PyPI Version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python Version"></a>
  <a href="https://github.com/asok-framework/asok/actions"><img src="https://img.shields.io/github/actions/workflow/status/asok-framework/asok/tests.yml?style=for-the-badge&label=tests" alt="Tests"></a>
  <a href="https://github.com/asok-framework/asok/issues"><img src="https://img.shields.io/github/issues/asok-framework/asok?style=for-the-badge&color=orange" alt="Issues"></a>
  <a href="https://github.com/asok-framework/asok/pulls"><img src="https://img.shields.io/github/issues-pr/asok-framework/asok?style=for-the-badge&color=blue" alt="Pull Requests"></a>
</p>

---

**Asok** is a cohesive, full-stack Python web framework designed for developer speed, elegant architecture, and security-conscious defaults. Built around a "zero-runtime-dependency" philosophy, it unifies server-side logic and client-side reactivity into a single, high-performance package, offering a streamlined development experience from the first line of code.

🌐 **[Official Website & Documentation](https://asok-framework.com)** | 📖 **[Quick Start Guide](https://asok-framework.com/docs/01-getting-started)** | 💬 **[Join Discord](https://discord.com/invite/aYYkuPT3qR)** | 🎥 **[YouTube Tutorials](https://www.youtube.com/@asok-framework)**

---

## 🎯 Why Asok?

### Zero Runtime Dependencies, Maximum Power
Asok requires **no external runtime dependencies** - just Python 3.10+. No Werkzeug, no Jinja2, no SQLAlchemy. The core framework is built from the Python standard library, making it:
- ✅ **Extremely lightweight** (~360KB)
- ✅ **Easy to audit** (everything in one codebase, no hidden dependencies)
- ✅ **Forever stable** (no dependency hell or supply chain risks)

### Modern Developer Experience
```python
# File-based routing like Next.js
src/pages/blog/[slug]/page.py  →  /blog/hello-world

# Client-side Reactivity
<div asok-state="{ count: 0 }">
  <button asok-on:click="count++" asok-text="count"></button>
</div>

# WebSocket Sync
class Counter(Component):
    count = 0

    @exposed
    def increment(self):
        self.count += 1

    def render(self):
        return self.html("counter.html")

# Admin interface in 2 lines
admin = Admin(app)
```

---

## ✨ Key Features

### Core Framework
- 💎 **Full Type Hints** - Complete PEP 484 support for IDE autocomplete
- ⌨️ **Powerful CLI** - Scaffolding, migrations, dev server, production builds
- 🛣️ **File-based Routing** - Next.js-style routing (`src/pages/` → URLs)
- ⛓️ **Dynamic Routes** - Parameters via `[id]`, `[slug:slug]` patterns

### Database & ORM
- 🗄️ **Built-in ORM** - SQLite (default), PostgreSQL, and MySQL support with relations, migrations, soft deletes
- 🔍 **Full-Text Search** - FTS5/FULLTEXT integration for lightning-fast search
- 🔐 **Auto Password Hashing** - PBKDF2-SHA256 with **600,000 iterations**
- 📊 **Query Builder** - Fluent API with eager loading

### Templates & Frontend
- 🎨 **Template Engine** - Jinja-compatible with inheritance and macros
- ⚡ **Reactive Components** - Client-side reactivity (< 3KB, no build step)
- 🔄 **Live Components** - Server-driven real-time updates via WebSockets
- 💨 **HTML Streaming** - Chunked responses for instant TTFB
- 🎭 **Transitions** - Built-in fade/slide/scale animations

### High-Performance APIs
- 🔌 **Native API Engine** - Build robust REST APIs with minimal code
- 📑 **Auto-OpenAPI** - Automatic OpenAPI 3.0 (Swagger) generation for every route
- 🛡️ **Bearer Token Auth** - Built-in secure authentication for stateless clients
- ⚡ **Optimized JSON** - High-speed serialization for high-throughput services
- 📑 **Live Documentation** - Interactive API explorer (Swagger UI) included

### Security
- 🔒 **CSRF Protection** - Auto-rotation, HMAC validation, SameSite=Strict
- 🔒 **XSS Prevention** - Auto-escaping templates, CSP nonces
- 🔒 **SQL Injection** - Parameterized queries, column validation
- 🔒 **Secure Sessions** - HttpOnly, Secure flags, HMAC-signed
- 🔒 **Path Traversal** - Absolute path validation
- 🔒 **OWASP Top 10** - Built-in protections for common web vulnerabilities

### Admin & Developer Tools
- 👨‍💼 **Auto Admin** - Django-inspired admin in 2 lines of code
- 🌍 **i18n Ready** - Multi-language support with JSON translations
- 📧 **Email Service** - SMTP integration with templates
- 📦 **Production Build** - Bytecode compilation, minification, WebP conversion
- 🧪 **Testing Tools** - Built-in test client, fixtures support

---

## 💭 Philosophy

Asok is designed for developers who want to build modern web applications without managing a complex stack of dependencies. It's a **cohesive toolkit** where everything works together out of the box—from database to real-time features—while remaining simple enough to understand and audit.

**Core Principles:**
- **Cohesion over Composition**: All components are designed to work together seamlessly
- **Simplicity over Magic**: Clear, readable code with minimal abstraction layers
- **Security by Default**: Strong security defaults are built in, with additional production hardening available through configuration
- **Developer Joy**: Fast feedback loops, intuitive APIs, excellent error messages

Asok doesn't aim to replace existing frameworks—it offers a different approach for teams who value simplicity, security, and rapid development in a unified environment.

---

## 🛠️ Installation & Setup

### 1. Installation
By default, Asok has zero external dependencies and works out of the box with SQLite:

```bash
pip install asok
```

If you wish to use optional database engines or the Redis backend (for caching and sessions), install the corresponding extra(s):

```bash
# Optional database engines & capabilities
pip install "asok[postgres]"
pip install "asok[mysql]"
pip install "asok[redis]"
pip install "asok[async]"

# Combined extras (e.g. Postgres + Redis)
pip install "asok[postgres,redis]"

```

or clone the repo and use the `asok/` folder.

### 2. Create a project

```bash
asok create my-project
cd my-project
```

### 3. Start the server
```bash
asok dev
```

---

## 🏗️ Project Structure

```text
├── src
│   ├── components                # Reactive components
│   ├── locales                   # JSON translations (en.json, fr.json, ...)
│   │   ├── en.json                  
│   │   └── fr.json
│   ├── middlewares               # Request interceptors
│   ├── models                    # ORM models (Post.py, User.py)
│   ├── pages                     # YOUR ROUTES (page.py, page.html)
│   │   ├── page.html
│   │   └── page.py
│   └── partials                  # css, js, images, html, uploads
│       ├── css
│       │   └── base.css
│       ├── html
│       │   └── base.html
│       ├── images
│       │   └── logo.svg
│       ├── js
│       │   └── base.js
│       └── uploads
└── wsgi.py                # Application entry point
```

---

## 🛣️ Routing
Routing is dictated by the structure of the `src/pages/` folder. Each folder represents a URL segment, and contains a `page.py` or `page.html` file.

- `src/pages/page.html` → `/`
- `src/pages/about/page.html` → `/about`
- `src/pages/user/[id]/page.py` → `/user/123` (`id` parameter)
- `src/pages/blog/[slug:slug]/page.py` → `/blog/my-post-slug`

### Dynamic Page Example (`src/pages/shop/[cat]/page.py`)
```python
from asok import Request 

def render(request: Request):
    category = request.params.get('cat')
    return f"Shop : {category}"
```

---

## 🎨 Templates & Inheritance
Templates in `src/pages/` can inherit from layouts in `src/partials/html/`.

**Layout (`src/partials/html/base.html`)** :
```html
<!DOCTYPE html>
<html lang="{{ request.lang }}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="{{ static('images/logo.svg') }}" type="image/svg+xml">
    <title>{% block title %}{% endblock %} &mdash; my-project</title>
    <link rel="stylesheet" href="{{ static('css/base.css') }}">
    <script defer src="{{ static('js/base.js') }}"></script>
</head>
<body>
    <main>{% block main %}{% endblock %}</main>
</body>
</html>
```

**Page (`src/pages/page.html`)** :
```html
{% extends "html/base.html" %}
{% block title %}Welcome{% endblock %}

{% block main %}
    <div class="container">
        <img src="{{ static('images/logo.svg') }}" alt="Logo Asok">
        <h1>Welcome to Asok</h1>
        <p>No dependencies—just Python’s standard library</p>
        <p>Edit <code>src/pages/page.html</code> to get started.</p>
    </div>
{% endblock %}

```

---

## 🗄️ AsokDB (The ORM)
Define your models in `src/models/`.

```python
from asok import Field, Model

class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password()
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    created_at = Field.CreatedAt()
```

---

## 🌍 i18n & Validation
- **Translation**: `{{ __('welcome') }}` (looks in `src/locales/`).
- **Validation**: `Validator(data).rule('email', 'required|email')`.
- **CSRF**: `{{ request.csrf_input() }}` automatic in forms.

---

## 🎨 Admin Customization

The administration interface is highly customizable:

```python
admin = Admin(app, site_name="My Platform", favicon="images/logo.svg")
```

### Asset Resolution (Smart Resolution)
The admin automatically detects the source of resources:
- **Internal Assets**: Files like `admin.css` or the default `logo.svg` are served from the package.
- **Project Assets**: If you specify a path (e.g. `images/logo.svg` or `uploads/icon.png`), the admin will serve them from your resources folder (`src/partials/`).

---

## 🚀 Towards Production
Asok supports both **WSGI** and **ASGI**. Use Gunicorn for WSGI or Uvicorn for ASGI:

```bash
# WSGI (Gunicorn)
gunicorn wsgi:app

# ASGI (Uvicorn) — for async/await support
uvicorn asgi:app
```

---

## 🔒 Production Security Checklist

Asok is built to be secure by default, but production environments require specific configurations to enable all protections.

### 1. Mandatory Environment Variables
In production (`DEBUG=False`), Asok enforces strict security checks:
- **`SECRET_KEY`**: Must be at least **32 characters** long. Use `secrets.token_hex(32)` to generate one.
- **`APP_URL`**: Required for Magic Links to prevent Host Header Injection. Example: `https://myapp.com`.

### 2. Secure Defaults
- **DEBUG**: Default is `False`. You must explicitly set `DEBUG=True` in your `.env` for development.
- **Password Hashing**: PBKDF2-SHA256 with **600,000 iterations**.
- **Security Headers**: HSTS (1 year), CSP (with nonces), X-Frame-Options (DENY), and X-Content-Type-Options (nosniff) are enabled by default.

### 3. Recommended .env for Production
```env
ASOK_ENV=production
DEBUG=false
SECRET_KEY=your-64-character-ultra-secure-key-here
APP_URL=https://yourdomain.com
DATABASE_URL=sqlite:///data/prod.db
```

---

## 🤝 Contributing

**We ❤️ contributions!** Asok is built to be simple, transparent, and fun to hack on. Whether you're a Python beginner or expert, there's a place for you here.

### 🌟 Ways to Contribute

- 🐛 **Report bugs** - Found an issue? [Open a bug report](https://github.com/asok-framework/asok/issues/new?template=bug_report.md)
- 💡 **Suggest features** - Have an idea? [Start a discussion](https://github.com/asok-framework/asok/discussions)
- 📝 **Improve docs** - Spot a typo? Docs are in [asok-docs](https://github.com/asok-framework/asok-docs)
- 🔧 **Submit PRs** - Fixed something? [Send a pull request](https://github.com/asok-framework/asok/pulls)
- ⭐ **Star the repo** - Show your support!
- 💬 **Help others** - Answer questions in [Discussions](https://github.com/asok-framework/asok/discussions)

### 🚀 Quick Start for Contributors

```bash
# 1. Fork and clone the repo
git clone https://github.com/YOUR_USERNAME/asok.git
cd asok

# 2. Create a virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# 3. Install dependencies (dev mode)
pip install -e .

# 4. Run the test suite
python -m pytest

# 5. Create a branch for your feature
git checkout -b feature/amazing-feature

# 6. Make your changes and test
python -m pytest -v

# 7. Commit and push
git commit -m "feat: add amazing feature"
git push origin feature/amazing-feature
```

**📖 Read our full [Contributing Guide](CONTRIBUTING.md)** for code style, commit conventions, and more.

### 🏆 Contributors

Thanks to all our amazing contributors! 🎉

<a href="https://github.com/asok-framework/asok/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=asok-framework/asok" />
</a>

---

## 💬 Support & Resources

**Need help?**
- 📚 Read the [documentation](https://asok-framework.com/docs)
- 🔍 Search [existing issues](https://github.com/asok-framework/asok/issues)
- 💬 Ask in [GitHub Discussions](https://github.com/asok-framework/asok/discussions)
- 🐛 Report bugs via [GitHub Issues](https://github.com/asok-framework/asok/issues/new)

**Documentation & Resources:**
- 📖 [Complete Framework Guide](https://asok-framework.com/docs)
- 📖 [Documentation Source](https://github.com/asok-framework/asok-docs) - Contribute to the docs
- 🛠️ [Code Examples](https://github.com/asok-framework/asok-examples) - Ready-to-use projects and templates
- 📖 [CHANGELOG](https://github.com/asok-framework/asok-docs/blob/main/CHANGELOG.md) - See what's new in each release

**Stay updated:**
- ⭐ Star the repo to follow development
- 👀 Watch releases for new versions

---

## 🗺️ Roadmap

Asok is actively developed with exciting features planned:

**v0.3.0** - Enterprise Ready ✅ **Released June 2026**
- **Async/ASGI**: Full async/await support with ASGI/WSGI dual engine
- **Multi-DB**: PostgreSQL & MySQL with connection pooling, vector search
- **Advanced ORM**: Polymorphic relations, self-referencing, nested eager loading, N+1 detection
- **WebSocket Rooms**: Multi-user collaboration with room broadcasting
- **Redis**: Caching, sessions, cache warming, fragment caching
- **Cloud**: AWS S3 storage integration
- **Background Jobs**: `asok worker` for async task processing
- **Admin Enhancements**: Inline editing, advanced filtering, saved presets, column customization
- **VSCode Extension**: Syntax highlighting, IntelliSense, snippets, route navigation
- **Localization**: Translation management UI and automatic string extraction
- **Query Optimization**: N+1 detection, query analysis, index suggestions, slow query logging

**v0.4.0** - GraphQL & Scale (Planned Q4 2026)
- GraphQL API with auto-generated schemas and subscriptions
- Advanced WebSocket features (presence, permissions, private messages)
- Multi-database scaling (read replicas, sharding, load balancing)
- Plugin ecosystem for third-party extensions
- Built-in monitoring & observability (Prometheus/Grafana)
- Advanced SSR & hydration (islands architecture, SSG, ISR)

**Note:** Timelines are subject to change based on community feedback and development priorities.

---

## 🏭 Production Status

Asok v0.3.0 is **actively developed software** with growing production adoption. It's suitable for:

**✅ Recommended for:**
- Production web applications and APIs
- Internal tools and admin dashboards
- Personal projects and MVPs
- Rapid prototyping and experimentation
- Learning full-stack Python development
- Projects requiring zero runtime dependencies
- Applications where dependency auditing is critical

**⚠️ Current Limitations:**
- **Ecosystem**: Growing community, limited third-party plugins
- **Maturity**: v0.3.x - APIs are stabilizing but may evolve before v1.0

**For mission-critical production applications**, Asok v0.3.0 provides enterprise features (async, multi-DB, Redis, S3) suitable for production workloads. Evaluate if the current feature set meets your specific requirements.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
