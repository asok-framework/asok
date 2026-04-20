# <img src="icons/favicon.svg" alt="Asok Framework Logo" width="50" align="center" /> Asok Framework

**Asok** is a powerful and elegant "zero-dependency" Python micro-framework. Version 0.1.0 introduces a professional modular architecture, file-based routing through the `src/pages/` directory, and a comprehensive CLI tool.

📖 **[→ Full Documentation](docs/README.md)** — 44 chapters, from installation to production deployment.

---

## ✨ Key Features
- **Zero Dependencies**: Relies exclusively on the Python standard library.
- **Professional Typing**: Full support for PEP 484 type hints for a robust developer experience.
- **Modular Package**: Install via `pip` or by simply dropping the `asok/` folder into your project.
- **CLI Tool**: Scaffolding (`asok make`), Dev Server (`asok dev`), and assets management.
- **Local Geolocation**: Built-in IP detection and localization without third-party APIs.
- **File-based Routing**: Your `src/pages/` directories define your URLs.
- **Dynamic Routing**: Native support for parameters via `[param]`.
- **Built-in Authentication**: Secure sessions via signed cookies (HMAC).
- **AsokDB**: A mini SQLite ORM with relationships and automatic hashing.
- **Template Engine**: Jinja-like syntax with inheritance (`extends`) and blocks.

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

## 📜 License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

<br><br>
<p align="left">
  <img src="icons/logo.svg" alt="Asok Framework" width="300" />
</p>


