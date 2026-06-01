from __future__ import annotations

import os
import shutil
from typing import Optional

from .style import Style


def _ensure_init_py(directory: str) -> None:
    """Create an empty __init__.py if it doesn't exist in the directory (and no .pyc exists)."""
    if not os.path.isdir(directory):
        return
    init_file = os.path.join(directory, "__init__.py")
    init_file_c = os.path.join(directory, "__init__.pyc")
    if not os.path.exists(init_file) and not os.path.exists(init_file_c):
        try:
            with open(init_file, "w"):
                pass
        except Exception:
            pass


def scaffold(
    app_name: str,
    tailwind: Optional[bool] = None,
    admin: Optional[bool] = None,
    image: Optional[bool] = None,
) -> None:
    """Create a new Asok project structure with optional features.

    SECURITY: Validates app_name to prevent path traversal attacks.
    """
    from .tools import image_install, tailwind_build, tailwind_install

    # SECURITY: Validate app_name (unless it's "." for current directory)
    if app_name != ".":
        if not app_name or not isinstance(app_name, str):
            Style.error("Invalid project name")
            return
        if len(app_name) > 100:
            Style.error("Project name too long (max 100 characters)")
            return
        # SECURITY: Prevent path traversal
        if ".." in app_name or "/" in app_name or "\\" in app_name:
            Style.error("Project name cannot contain path separators or '..'")
            return
        # SECURITY: Validate characters
        if not app_name.replace("_", "").replace("-", "").isalnum():
            Style.error(
                "Project name must contain only letters, numbers, hyphens, and underscores"
            )
            return

    if tailwind is None:
        tailwind = Style.confirm("Add Tailwind CSS support?")
    if admin is None:
        admin = Style.confirm("Add Admin interface?")
    if image is None:
        image = Style.confirm("Add Image Optimization (WebP)?")

    if app_name == ".":
        root = os.getcwd()
        app_name = os.path.basename(root)
    else:
        root = os.path.join(os.getcwd(), app_name)
        os.makedirs(root, exist_ok=True)

    print(
        f"\n{Style.BOLD}{Style.CYAN}🚀 Creating Asok project: {Style.GREEN}{app_name}{Style.RESET}..."
    )

    for d in [
        "src",
        "src/components",
        "src/locales",
        "src/middlewares",
        "src/models",
        "src/pages",
        "src/partials/css",
        "src/partials/html",
        "src/partials/images",
        "src/partials/js",
        "src/partials/uploads",
        "src/migrations",
    ]:
        dir_path = os.path.join(root, d)
        os.makedirs(dir_path, exist_ok=True)
        if d in (
            "src",
            "src/components",
            "src/middlewares",
            "src/models",
            "src/pages",
            "src/migrations",
        ):
            _ensure_init_py(dir_path)

    def write(path, content):
        with open(os.path.join(root, path), "w", encoding="utf-8") as f:
            f.write(content)

    if admin:
        write(
            "wsgi.py",
            "from asok import Asok\nfrom asok.admin import Admin\n\napp = Asok()\nAdmin(app)\n",
        )
    else:
        write("wsgi.py", "from asok import Asok\n\napp = Asok()\n")
    write(".env", "DEBUG=true\nSECRET_KEY=change-me-in-production\n")
    write(
        ".gitignore",
        "__pycache__/\n*.py[cod]\nvenv/\n.venv/\ndb.sqlite3\ndb.sqlite3-shm\ndb.sqlite3-wal\n.env\n.DS_Store\n.asok/\nsrc/partials/uploads/\nsrc/partials/css/base.build.css\n",
    )
    write("src/partials/js/base.js", "")
    write("src/partials/uploads/.gitkeep", "")
    write("src/locales/en.json", "{}\n")
    write("src/locales/fr.json", "{}\n")

    assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")

    shutil.copy2(
        os.path.join(assets_dir, "logo.svg"),
        os.path.join(root, "src/partials/images/logo.svg"),
    )

    css_link = "css/base.build.css" if tailwind else "css/base.css"
    write(
        "src/partials/html/base.html",
        f"""\
<!DOCTYPE html>
<html lang="{{{{ request.lang }}}}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="icon" href="{{{{ static('images/logo.svg') }}}}" type="image/svg+xml">
    <title>{{% block title %}}{{% endblock %}} &mdash; {app_name}</title>
    <link rel="stylesheet" href="{{{{ static('{css_link}') }}}}">
    <script type="module" src="{{{{ static('js/base.js') }}}}" nonce="{{{{ request.nonce }}}}"></script>
</head>
<body>
    <main>{{% block main %}}{{% endblock %}}</main>
</body>
</html>
""",
    )

    if tailwind:
        write(
            "src/partials/css/base.css",
            '@import "tailwindcss";\n',
        )
    else:
        write(
            "src/partials/css/base.css",
            """\
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
  background: #0f172a;
  color: #e2e8f0;
  line-height: 1.6;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}

.container {
  max-width: 800px;
  padding: 40px 20px;
  text-align: center;
}

img {
  width: 6rem;
  height: auto;
  margin-bottom: 1.5rem;
  opacity: 0.9;
}

h1 {
  font-size: 3rem;
  font-weight: 700;
  margin-bottom: 1rem;
  letter-spacing: -0.02em;
}

p {
  font-size: 1.1rem;
  color: #94a3b8;
  margin-bottom: 1rem;
}

code {
  background: #1e293b;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 0.95rem;
  color: #38bdf8;
  transition: background 0.2s ease;
}

code:hover {
  background: #334155;
}
""",
        )

    write(
        "src/pages/page.py",
        "from asok import Request\n\ndef render(request: Request):\n    return request.html('page.html')\n",
    )
    if tailwind:
        write(
            "src/pages/page.html",
            """\
{% extends "html/base.html" %}
{% block title %}Welcome{% endblock %}

{% block main %}
    <div class="min-h-screen flex items-center justify-center bg-slate-900 text-slate-200 font-sans">
        <div class="max-w-2xl mx-auto px-5 py-10 text-center">
            <img class="w-24 mx-auto mb-6 opacity-90" src="{{ static('images/logo.svg') }}" alt="Logo Asok">
            <h1 class="text-5xl font-bold mb-4 tracking-tight">Welcome to Asok</h1>
            <p class="text-lg text-slate-400 mb-4">No dependencies—just Python’s standard library</p>
            <p class="text-slate-400">
                Edit
                <code class="bg-slate-800 text-sky-400 px-2 py-1 rounded-md text-sm hover:bg-slate-700 transition">
                    src/pages/page.html
                </code>
                to get started.
            </p>
        </div>
    </div>
{% endblock %}
""",
        )
    else:
        write(
            "src/pages/page.html",
            """\
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
""",
        )

    if admin:
        write(
            "src/models/user.py",
            """\
from asok import Field, Model


class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password()
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    totp_secret = Field.String(nullable=True, hidden=True)
    totp_enabled = Field.Boolean(default=False)
    backup_codes = Field.String(nullable=True, hidden=True)
    created_at = Field.CreatedAt()
""",
        )

    if tailwind:
        print()
        try:
            tailwind_install(root, verbose=True)
            tailwind_build(root, minify=False)
        except Exception as e:
            Style.error(f"Tailwind setup failed: {e}")
            print(
                f"  {Style.DIM}You can retry later with: asok tailwind --install{Style.RESET}\n"
            )

    if image:
        print()
        try:
            image_install(root, verbose=True)
            with open(os.path.join(root, ".env"), "a") as f:
                f.write("\nIMAGE_OPTIMIZATION=true\n")
        except Exception as e:
            Style.warn(f"Image optimization setup failed: {e}")

    print(
        f"\n  {Style.GREEN}{Style.BOLD}✅ Project '{app_name}' created!{Style.RESET}\n"
    )
    if root != os.getcwd():
        print(f"  {Style.DIM}$ cd {app_name}{Style.RESET}")
    print(f"  {Style.DIM}$ asok dev{Style.RESET}\n")
