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

**Asok** is a powerful and elegant "zero-dependency" Python web framework that brings modern development patterns to Python. Built with security-first principles, it combines the simplicity of Flask with the batteries-included approach of Django, while introducing Next.js-style file-based routing.

🌐 **[Official Website & Documentation](https://asok-framework.com)** | 📖 **[Quick Start Guide](https://asok-framework.com/docs/01-getting-started)** | 💬 **[Join Discord](https://discord.com/invite/aYYkuPT3qR)** | 🎥 **[YouTube Tutorials](https://www.youtube.com/@asok-framework)**

---

## 🎯 Why Asok?

### Zero Dependencies, Maximum Power
Unlike other Python frameworks, Asok requires **zero external dependencies** - just Python 3.10+. No Werkzeug, no Jinja2, no SQLAlchemy. Everything is built from the Python standard library, making it:
- ✅ **Extremely lightweight** (~200KB)
- ✅ **Dead simple to audit** (security teams love it)
- ✅ **Forever stable** (no dependency hell)
- ✅ **Fast to install** (< 1 second)

### Modern Developer Experience
```python
# File-based routing like Next.js
src/pages/blog/[slug]/page.py  →  /blog/hello-world

# Reactive components out of the box
<div asok-state="{ count: 0 }">
  <button asok-on:click="count++">{{ count }}</button>
</div>

# Admin interface in 2 lines
admin = Admin(app)
```

### Production-Ready Security
- 🔒 **OWASP Top 10** protections built-in
- 🔒 **Automatic CSRF** tokens with rotation
- 🔒 **SQL injection** prevention via parameterized queries
- 🔒 **XSS protection** with auto-escaping templates
- 🔒 **Secure sessions** (HttpOnly, SameSite=Strict, HMAC-signed)
- 🔒 **10/10 security score** in comprehensive audits

---

## ✨ Key Features

### Core Framework
- 🚀 **Zero Dependencies** - Pure Python stdlib, no external packages
- 💎 **Full Type Hints** - Complete PEP 484 support for IDE autocomplete
- 📦 **Tiny Footprint** - ~200KB, installs in < 1 second
- ⌨️ **Powerful CLI** - Scaffolding, migrations, dev server, production builds
- 🛣️ **File-based Routing** - Next.js-style routing (`src/pages/` → URLs)
- ⛓️ **Dynamic Routes** - Parameters via `[id]`, `[slug:slug]` patterns

### Database & ORM
- 🗄️ **Built-in ORM** - SQLite with relations, migrations, soft deletes
- 🔍 **Full-Text Search** - FTS5 integration for lightning-fast search
- 🔐 **Auto Password Hashing** - PBKDF2-SHA256 with 100k iterations
- 📊 **Query Builder** - Fluent API with eager loading

### Templates & Frontend
- 🎨 **Template Engine** - Jinja-compatible with inheritance and macros
- ⚡ **Reactive Components** - Client-side reactivity (< 3KB, no build step)
- 🔄 **Live Components** - Server-driven real-time updates via WebSockets
- 💨 **HTML Streaming** - Chunked responses for instant TTFB
- 🎭 **Transitions** - Built-in fade/slide/scale animations

### Security (10/10 Score)
- 🔒 **CSRF Protection** - Auto-rotation, HMAC validation, SameSite=Strict
- 🔒 **XSS Prevention** - Auto-escaping templates, CSP nonces
- 🔒 **SQL Injection** - Parameterized queries, column validation
- 🔒 **Secure Sessions** - HttpOnly, Secure flags, HMAC-signed
- 🔒 **Path Traversal** - Absolute path validation

### Admin & Developer Tools
- 👨‍💼 **Auto Admin** - Django-style admin in 2 lines of code
- 🌍 **i18n Ready** - Multi-language support with JSON translations
- 📧 **Email Service** - SMTP integration with templates
- 📦 **Production Build** - Bytecode compilation, minification, WebP conversion
- 🧪 **Testing Tools** - Built-in test client, fixtures support

---

## ⚖️ Asok vs Django vs Flask

Asok was designed to bring the best of both worlds (the lightweight nature of Flask and the batteries-included approach of Django), while adding modern file-based routing (inspired by Next.js/SvelteKit).

| Feature | Asok | Flask | Django |
|---|---|---|---|
| **External Dependencies** | **0 (Zero)** | ~6 (Werkzeug, Jinja...) | ~3 (asgiref, sqlparse...) |
| **Philosophy** | Batteries Included + Modern | Micro-framework | Megalo-framework |
| **Routing System** | **File-based** (`src/pages/`) | Decorators (`@app.route`) | Centralized (`urls.py`) |
| **Built-in ORM** | Yes (AsokDB - optimized SQLite) | No (SQLAlchemy required) | Yes (Full-featured, multi-DB) |
| **Generated Admin** | Yes, 100% automatic and reactive | No (Flask-Admin required) | Yes, historical and heavy |
| **Real-time (WebSockets)** | **Native** (Alive Engine) | No (Flask-SocketIO required) | Complex (Django Channels) |
| **Reactive Components** | **Native** (Live Components) | No | No |
| **Ideal for** | Fast projects, Modern SaaS, Zero devops | Simple APIs, Microservices | Large legacy architectures |

---

## 🛠️ Installation & Setup

### 1. Installation
You can install Asok via pip:

```bash
pip install asok
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
Asok is WSGI compatible. You can use Gunicorn or any other WSGI server:
```bash
gunicorn wsgi:app
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

# 4. Run the test suite (353 tests should pass!)
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

## 🌐 Ecosystem

Explore the Asok ecosystem:
- 🛠️ **[Asok Examples](https://github.com/asok-framework/asok-examples)** - Ready-to-use projects and templates
- 🧪 **[Asok Lab](https://github.com/asok-framework/asok-lab)** - Experimental features, benchmarks, playground
- 📖 **[Asok Docs](https://github.com/asok-framework/asok-docs)** - Documentation and website source
- 🎓 **[Asok Tutorials](https://www.youtube.com/@asok-framework)** - Step-by-step learning paths

---

## 💬 Community & Support

Join our growing community:

- 💬 **[Discord Server](https://discord.gg/asok)** - Real-time chat, help, and discussions
- 🐦 **[Twitter/X](https://twitter.com/asok_framework)** - News and updates
- 📖 **[GitHub Discussions](https://github.com/asok-framework/asok/discussions)** - Q&A, feature requests, show & tell
- 🎥 **[YouTube Channel](https://www.youtube.com/@asok-framework)** - Tutorials and demos

**Need help?**
- 📚 Check the [documentation](https://asok-framework.com/docs)
- 🔍 Search [existing issues](https://github.com/asok-framework/asok/issues)
- 💬 Ask in [Discord](https://discord.com/invite/aYYkuPT3qR) or [Discussions](https://github.com/asok-framework/asok/discussions)

---

## 🗺️ Roadmap

Asok is actively developed with exciting features planned:

**v0.2.0 (Q2 2026)** - Enterprise Features
- PostgreSQL & MySQL support, advanced ORM relationships
- WebSocket rooms for real-time collaboration
- Background job queue system
- Plugin ecosystem & CLI enhancements

**v0.3.0 (Q3 2026)** - Modern Stack
- GraphQL API support with auto-generated schemas
- Server-side rendering (SSR) & static site generation
- Built-in monitoring & observability tools
- Full async/await support (ASGI)

See the **[detailed roadmap](ROADMAP.md)** for complete feature lists, timelines, and how to contribute to planning.

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=asok-framework/asok&type=Date)](https://star-history.com/#asok-framework/asok&Date)

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
