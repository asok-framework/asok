# CLI

The `asok` command-line tool provides a professional, styled interface to help you create, develop, and manage your project.

## Commands

### `asok create <name>`

Create a new project. Asok features a **Smart Interactive Mode**: if you don't provide any flags, it will guide you through the setup with questions.

```bash
asok create myapp
#   ? Add Tailwind CSS support? [y/N]: y
#   ? Add Admin interface? [y/N]: y
#
# 🚀 Creating Asok project: myapp...
#   ✅ Project 'myapp' created!
```

#### Flags (Direct Mode)
Skip questions by providing flags explicitly:
```bash
asok create myapp --tailwind --admin
```

### `asok createsuperuser`

Create an administrative account. Now uses a conversational, professional prompt system:

```bash
asok createsuperuser
#   CREATE SUPERUSER
#   Enter your email address: admin@example.com
#   Enter your password: 
#   Confirm your password: 
#   ✅ Created 'admin' role with full permissions.
#   ✅ Superuser created: admin@example.com
```

### `asok dev`

Start the development server with auto-reload and **live browser reload**:

```bash
asok dev
#
# DEVELOPMENT SERVER
#   Reloader ● Active (PID: 12345)
#   URL      http://127.0.0.1:8000
#   Tailwind ● Watching...
```

Watches `.py`, `.html`, `.json`, `.css`, `.js` files and **`.env`** across the project (root, `src/`, and `asok/`).

### `asok migrate`

Auto-detect new columns in your models and add them to the database:

```bash
asok migrate
#
# MIGRATIONS
#   + posts.category (TEXT)
#   + posts.views (INTEGER)
#   ✅ Migrations applied: 2 change(s).
```

### `asok seed`

Populate the database with test data:

```bash
asok seed
#
# SEEDING DATA
#   ✅ Seeding complete.
```

### `asok routes`

List all registered routes in a clean, tabular format:

```bash
asok routes
#
# ROUTES
#   URL          HANDLER
#   -----------   ---------------
#   /             page.py
#   /about        page.html
#   /blog/[slug]  page.py
```

### `asok shell`

Open an interactive Python shell with the `app` instance and all your models pre-imported:

```bash
asok shell
#
# Asok Shell (Interactive Python)
# ℹ️ All models and 'app' instance pre-imported.
#
# Asok shell — models loaded: User, Post, Comment
# Python 3.12
>>> User.count()
```

### `asok image`

Manage your Image Optimization system (WebP conversion).

- `asok image --install`: Download the `cwebp` standalone binary.
- `asok image --enable`: **Enable optimization on an existing project**. Installs binary and sets `IMAGE_OPTIMIZATION=true` in `.env`.
- `asok image --optimize`: **Full project scan**. Converts all existing JPG/PNG images in `src/partials/` and `src/uploads/` to WebP.

### `asok tailwind`

Manage your Tailwind CSS integration.

- `asok tailwind --install`: Download the binary.
- `asok tailwind --build`: Run a one-shot build.
- `asok tailwind --enable`: **Setup Tailwind on an existing project** (adds imports to CSS and links to HTML).

### `asok admin`

Manage the Admin interface.

- `asok admin --enable`: **Retro-fit the Admin interface** into an existing project. Handles `wsgi.py` updates and `User` model scaffolding.

### `asok make <type> <name>`

Scaffold new components:
- `asok make model User`
- `asok make middleware Auth`
- `asok make page Dashboard`
- `asok make component SearchBar`

### `asok preview`

Run the application in production mode locally:

```bash
asok preview
#
# PREVIEW SERVER (PRODUCTION MODE)
#   URL  http://127.0.0.1:8000
# ℹ️ No auto-reload — restart manually after changes
```

---
[← Previous: Testing](13-testing.md) | [Documentation](README.md) | [Next: CORS & Gzip →](15-cors-gzip.md)
