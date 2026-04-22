<p align="center">
  <img src="https://raw.githubusercontent.com/asok-framework/asok/main/icons/logo.svg" alt="Asok Framework Logo" width="400" />
</p>

<p align="center">
  <a href="https://github.com/asok-framework/asok/stargazers"><img src="https://img.shields.io/github/stars/asok-framework/asok?style=for-the-badge&color=ffd700" alt="GitHub Stars"></a>
  <a href="https://github.com/asok-framework/asok/blob/main/LICENSE"><img src="https://img.shields.io/github/license/asok-framework/asok?style=for-the-badge&color=4169e1" alt="License"></a>
  <a href="https://pypi.org/project/asok/"><img src="https://img.shields.io/pypi/v/asok?style=for-the-badge&color=228b22" alt="PyPI Version"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.7+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python Version"></a>
</p>

---

**Asok** is a powerful and elegant "zero-dependency" Python micro-framework. It introduces a professional modular architecture, file-based routing, and a comprehensive CLI tool designed for speed and simplicity.

🌐 **[Official Website & Documentation](https://asok-framework.com)**

---

## ✨ Key Features

- 🚀 **Zero Dependencies**: Relies exclusively on the Python standard library.
- 💎 **Professional Typing**: Full support for PEP 484 type hints for a robust developer experience.
- 📦 **Modular Package**: Install via `pip` or by simply dropping the `asok/` folder into your project.
- ⌨️ **Powerful CLI**: Scaffolding (`asok make`), Dev Server (`asok dev`), and assets management.
- 🌍 **Local Geolocation**: Built-in IP detection and localization without third-party APIs.
- 🛣️ **File-based Routing**: Your `src/pages/` directories define your URLs.
- ⛓️ **Dynamic Routing**: Native support for parameters via `[param]`.
- 🔐 **Built-in Auth**: Secure sessions via signed cookies (HMAC).
- 🗄️ **AsokDB**: A minimalist SQLite ORM with relationships and automatic hashing.
- 🎨 **Template Engine**: Jinja-like syntax with inheritance (`extends`) and blocks.
- ⚡ **Smart Streaming**: Ultra-fast HTML streaming with on-the-fly asset injection.
- 💾 **Component Persistence**: Reactive component state preserved across page navigations.

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
Contributions are more than welcome! Asok is built to be simple and transparent, making it a great codebase to dive into.
- Found a bug? Open an **Issue**.
- Have a feature idea? Start a **Discussion**.
- Fixed something? Submit a **Pull Request**.

Make sure to run the tests and linter before submitting your PR:
```bash
make lint
make test
```

---

## 🌐 Ecosystem

Explore the Asok ecosystem:
- 🛠️ **[Asok Examples](https://github.com/asok-framework/asok-examples)**: A collection of ready-to-use projects and templates.
- 🧪 **[Asok Lab](https://github.com/asok-framework/asok-lab)**: Experimental features, benchmarks, and playground.
- 📖 **[Asok Docs & Website Source](https://github.com/asok-framework/asok-docs)**: The source code for the documentation and the official website.

---

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
