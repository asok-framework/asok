from __future__ import annotations

import argparse
import code
import getpass
import importlib.util as _ilu
import os
import platform as _p
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.request
import zipfile
from io import BytesIO
from typing import Optional
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from . import __version__
from .orm import MODELS_REGISTRY, Model
from .utils.minify import minify_html

TAILWIND_VERSION = "4.2.2"
IMAGE_VERSION = "1.5.0"
ASSETS_VERSION = "0.25.0"


class _QuietHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        if self.path == "/__reload":
            return
        super().log_request(code, size)

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            # Browser closed the connection prematurely (e.g. refresh)
            pass
        except Exception:
            traceback.print_exc()


class Style:
    """ANSI color styles and utility methods for professional terminal output."""

    HEADER = "\033[95m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @staticmethod
    def success(msg: str) -> None:
        """Print a success message with a green checkmark."""
        print(f"  {Style.GREEN}✅ {msg}{Style.RESET}")

    @staticmethod
    def info(msg: str) -> None:
        """Print an informational message with a cyan icon."""
        print(f"  {Style.CYAN}ℹ️ {msg}{Style.RESET}")

    @staticmethod
    def warn(msg: str) -> None:
        """Print a warning message with a yellow icon."""
        print(f"  {Style.YELLOW}⚠ {msg}{Style.RESET}")

    @staticmethod
    def error(msg: str) -> None:
        """Print an error message with a red icon."""
        print(f"  {Style.RED}✖ {msg}{Style.RESET}")

    @staticmethod
    def heading(msg: str) -> None:
        """Print a bold blue heading."""
        print(f"\n{Style.BOLD}{Style.BLUE}{msg}{Style.RESET}")

    @staticmethod
    def confirm(question: str, default: bool = False) -> bool:
        """Ask a Y/n question interactively and return the boolean response."""
        hint = " [Y/n]" if default else " [y/N]"
        try:
            ans = (
                input(
                    f"  {Style.BOLD}{Style.CYAN}?{Style.RESET} {question}{Style.DIM}{hint}{Style.RESET}: "
                )
                .strip()
                .lower()
            )
            if not ans:
                return default
            return ans in ("y", "yes")
        except (KeyboardInterrupt, EOFError):
            print()
            return default


def scaffold(
    app_name: str,
    tailwind: Optional[bool] = None,
    admin: Optional[bool] = None,
    image: Optional[bool] = None,
) -> None:
    """Create a new Asok project structure with optional features."""
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
    ]:
        os.makedirs(os.path.join(root, d), exist_ok=True)

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

    assets_dir = os.path.join(os.path.dirname(__file__), "assets")

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
    <script defer src="{{{{ static('js/base.js') }}}}" nonce="{{{{ request.nonce }}}}"></script>
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


# ── Tailwind support ─────────────────────────────────────────

_TAILWIND_PLATFORMS = {
    ("Darwin", "arm64"): "macos-arm64",
    ("Darwin", "x86_64"): "macos-x64",
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Linux", "arm64"): "linux-arm64",
    ("Windows", "AMD64"): "windows-x64.exe",
    ("Windows", "x86_64"): "windows-x64.exe",
}
_IMAGE_PLATFORMS = {
    ("Darwin", "arm64"): "mac-arm64",
    ("Darwin", "x86_64"): "mac-x86-64",
    ("Linux", "x86_64"): "linux-x86-64",
    ("Linux", "aarch64"): "linux-aarch64",
    ("Linux", "arm64"): "linux-aarch64",
    ("Windows", "AMD64"): "windows-x64",
    ("Windows", "x86_64"): "windows-x64",
}


_ESBUILD_PLATFORMS = {
    ("Darwin", "arm64"): "darwin-arm64",
    ("Darwin", "x86_64"): "darwin-x64",
    ("Linux", "x86_64"): "linux-x64",
    ("Linux", "aarch64"): "linux-arm64",
    ("Linux", "arm64"): "linux-arm64",
    ("Windows", "AMD64"): "win32-x64",
    ("Windows", "x86_64"): "win32-x64",
}


def _tailwind_platform_suffix():
    key = (_p.system(), _p.machine())
    suffix = _TAILWIND_PLATFORMS.get(key)
    if not suffix:
        raise RuntimeError(
            f"No Tailwind binary for {key[0]}/{key[1]}. "
            f"Install manually from https://github.com/tailwindlabs/tailwindcss/releases"
        )
    return suffix


def _project_uses_tailwind(root):
    css_path = os.path.join(root, "src/partials/css/base.css")
    if not os.path.isfile(css_path):
        return False
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            return '@import "tailwindcss"' in f.read()
    except OSError:
        return False


def _find_project_root(start=None):
    cur = start or os.getcwd()
    for _ in range(10):
        if os.path.isfile(os.path.join(cur, "wsgi.py")) or os.path.isfile(
            os.path.join(cur, "wsgi.pyc")
        ):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


def _tailwind_binary_path(root):
    suffix = _tailwind_platform_suffix()
    name = "tailwindcss.exe" if suffix.endswith(".exe") else "tailwindcss"
    return os.path.join(root, ".asok", "bin", name)


def _tailwind_version_file(root):
    return os.path.join(root, ".asok", "bin", "version.txt")


def tailwind_install(root, verbose=True):
    """Download the pinned Tailwind binary into .asok/bin/ if missing or outdated."""
    import urllib.request

    suffix = _tailwind_platform_suffix()
    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)

    bin_path = _tailwind_binary_path(root)
    version_path = _tailwind_version_file(root)

    current_version = None
    if os.path.isfile(version_path):
        try:
            with open(version_path) as f:
                current_version = f.read().strip()
        except OSError:
            pass

    if os.path.isfile(bin_path) and current_version == TAILWIND_VERSION:
        if verbose:
            print(f"  Tailwind v{TAILWIND_VERSION} already installed")
        return bin_path

    url = (
        f"https://github.com/tailwindlabs/tailwindcss/releases/download/"
        f"v{TAILWIND_VERSION}/tailwindcss-{suffix}"
    )
    if verbose:
        print(f"  Downloading Tailwind v{TAILWIND_VERSION} ({suffix})...")

    try:
        urllib.request.urlretrieve(url, bin_path)
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")

    if not suffix.endswith(".exe"):
        os.chmod(bin_path, 0o755)

    with open(version_path, "w") as f:
        f.write(TAILWIND_VERSION)

    if verbose:
        Style.success("Installed to .asok/bin/")
    return bin_path


def tailwind_build(root, minify=False):
    """Run a one-shot Tailwind build."""
    bin_path = _tailwind_binary_path(root)
    if not os.path.isfile(bin_path):
        raise RuntimeError("Tailwind not installed. Run: asok tailwind --install")

    input_path = os.path.join(root, "src/partials/css/base.css")
    output_path = os.path.join(root, "src/partials/css/base.build.css")

    cmd = [bin_path, "-i", input_path, "-o", output_path]
    if minify:
        cmd.append("--minify")

    print(f"  Building CSS{' (minified)' if minify else ''}...")
    result = subprocess.run(cmd, cwd=root)
    if result.returncode != 0:
        Style.error("Tailwind build failed")
        raise RuntimeError("Tailwind build failed")
    Style.success(f"Built {os.path.relpath(output_path, root)}")


def tailwind_enable(root):
    """Enable Tailwind CSS in an existing project."""
    Style.heading("ENABLING TAILWIND CSS")

    # 1. Ensure src/partials/css/base.css has @import "tailwindcss"
    css_path = os.path.join(root, "src/partials/css/base.css")
    os.makedirs(os.path.dirname(css_path), exist_ok=True)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write('@import "tailwindcss";\n')
    Style.success("Reset src/partials/css/base.css with Tailwind import")

    # 2. Update src/partials/html/base.html to use base.build.css
    base_html = os.path.join(root, "src/partials/html/base.html")
    if os.path.isfile(base_html):
        with open(base_html, "r", encoding="utf-8") as f:
            html = f.read()

        old_link = "href=\"{{ static('css/base.css') }}\""
        new_link = "href=\"{{ static('css/base.build.css') }}\""

        if old_link in html:
            html = html.replace(old_link, new_link)
            with open(base_html, "w", encoding="utf-8") as f:
                f.write(html)
            Style.success("Updated base.html to use compiled CSS")

    # 3. Install and build
    tailwind_install(root, verbose=True)
    tailwind_build(root, minify=False)
    Style.success("Tailwind CSS is now enabled and ready!")


def admin_enable(root):
    """Enable Admin interface in an existing project."""
    Style.heading("ENABLING ADMIN INTERFACE")

    # 1. Update wsgi.py
    wsgi_path = os.path.join(root, "wsgi.py")
    if os.path.isfile(wsgi_path):
        with open(wsgi_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        has_import = any("from asok.admin import Admin" in line for line in lines)
        has_init = any("Admin(app)" in line for line in lines)

        if not has_import:
            lines.insert(0, "from asok.admin import Admin\n")

        if not has_init:
            new_lines = []
            inserted = False
            for line in lines:
                new_lines.append(line)
                if "app = Asok()" in line and not inserted:
                    new_lines.append("Admin(app)\n")
                    inserted = True
            lines = new_lines

        with open(wsgi_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        Style.success("Updated wsgi.py to include Admin(app)")

    # 2. Check for src/models/user.py
    user_model = os.path.join(root, "src/models/user.py")
    if not os.path.isfile(user_model):
        os.makedirs(os.path.dirname(user_model), exist_ok=True)
        with open(user_model, "w", encoding="utf-8") as f:
            f.write("""from asok import Field, Model

class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password()
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    created_at = Field.CreatedAt()
""")
        Style.success("Created default User model in src/models/user.py")

    Style.info("Next steps:")
    print("  1. Run 'asok migrate' to create the user table")
    print("  2. Run 'asok createsuperuser' to create your first account")
    print("  3. Visit /admin in your browser\n")


def _image_binary_path(root):
    suffix = _tailwind_platform_suffix()
    name = "cwebp.exe" if suffix.endswith(".exe") else "cwebp"
    return os.path.join(root, ".asok", "bin", name)


def image_install(root, verbose=True):
    """Download and extract libwebp cwebp binary."""
    key = (_p.system(), _p.machine())
    os_suffix = _IMAGE_PLATFORMS.get(key)
    if not os_suffix:
        raise RuntimeError(f"No libwebp binary for {key}")

    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bin_path = _image_binary_path(root)

    # Check version
    ver_file = os.path.join(bin_dir, "image_version.txt")
    if (
        os.path.exists(bin_path)
        and os.path.exists(ver_file)
        and open(ver_file).read().strip() == IMAGE_VERSION
    ):
        if verbose:
            print(f"  libwebp v{IMAGE_VERSION} already installed")
        return bin_path

    ext = "zip" if "windows" in os_suffix else "tar.gz"
    url = (
        f"https://storage.googleapis.com/downloads.webmproject.org/releases/webp/"
        f"libwebp-{IMAGE_VERSION}-{os_suffix}.{ext}"
    )

    if verbose:
        print(f"  Downloading libwebp v{IMAGE_VERSION} ({os_suffix})...")

    try:
        resp = urllib.request.urlopen(url)
        buf = BytesIO(resp.read())
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")

    if ext == "zip":
        with zipfile.ZipFile(buf) as z:
            for name in z.namelist():
                if name.endswith("cwebp.exe"):
                    with open(bin_path, "wb") as f:
                        f.write(z.read(name))
                    break
    else:
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            for member in t.getmembers():
                if member.name.endswith("/cwebp"):
                    f = t.extractfile(member)
                    with open(bin_path, "wb") as f_out:
                        f_out.write(f.read())
                    os.chmod(bin_path, 0o755)
                    break

    with open(ver_file, "w") as f:
        f.write(IMAGE_VERSION)

    if verbose:
        Style.success("Installed cwebp to .asok/bin/")
    return bin_path


def image_enable(root):
    """Enable Image Optimization in an existing project."""
    Style.heading("ENABLING IMAGE OPTIMIZATION")
    image_install(root, verbose=True)

    env_path = os.path.join(root, ".env")
    current_env = ""
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            current_env = f.read()

    if "IMAGE_OPTIMIZATION" not in current_env:
        with open(env_path, "a") as f:
            f.write("\nIMAGE_OPTIMIZATION=true\n")
        Style.success("Enabled IMAGE_OPTIMIZATION=true in .env")

    Style.info("Next steps:")
    print("  1. Asok will now automatically optimize uploaded images")
    print("  2. Existing images can be optimized manually if needed\n")


def image_optimize_all(root, delete_originals=False):
    """Scan and optimize all existing images in the project."""
    from asok.utils.image import is_image, optimize_image

    Style.heading("OPTIMIZING EXISTING IMAGES")
    count = 0

    # Scannable dirs
    target_dirs = [
        os.path.join(root, "src/partials"),
    ]

    for base_dir in target_dirs:
        if not os.path.exists(base_dir):
            continue

        for r, d, files in os.walk(base_dir):
            for f in files:
                path = os.path.join(r, f)
                if is_image(path):
                    # Check if webp already exists
                    if os.path.exists(path + ".webp"):
                        if delete_originals:
                            try:
                                os.remove(path)
                            except Exception:
                                pass
                        continue

                    print(f"  Optimizing {os.path.relpath(path, root)}...")
                    if optimize_image(
                        path, root=root, keep_original=not delete_originals
                    ):
                        count += 1

    Style.success(f"Optimized {count} image(s)")


def _esbuild_binary_path(root):
    suffix = _tailwind_platform_suffix()
    name = "esbuild.exe" if suffix.endswith(".exe") else "esbuild"
    return os.path.join(root, ".asok", "bin", name)


def assets_install(root, verbose=True):
    """Download and extract esbuild binary from npm registry."""

    key = (_p.system(), _p.machine())
    npm_pkg = _ESBUILD_PLATFORMS.get(key)
    if not npm_pkg:
        raise RuntimeError(f"No esbuild binary for {key}")

    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bin_path = _esbuild_binary_path(root)

    # Check version
    ver_file = os.path.join(bin_dir, "assets_version.txt")
    if (
        os.path.exists(bin_path)
        and os.path.exists(ver_file)
        and open(ver_file).read().strip() == ASSETS_VERSION
    ):
        if verbose:
            print(f"  Esbuild v{ASSETS_VERSION} already installed")
        return bin_path

    url = (
        f"https://registry.npmjs.org/@esbuild/{npm_pkg}/-/"
        f"{npm_pkg}-{ASSETS_VERSION}.tgz"
    )

    if verbose:
        print(f"  Downloading Esbuild v{ASSETS_VERSION} ({npm_pkg})...")

    try:
        resp = urllib.request.urlopen(url)
        buf = BytesIO(resp.read())
        with tarfile.open(fileobj=buf, mode="r:gz") as t:
            for member in t.getmembers():
                # npm packages store content in 'package/'
                if member.name.endswith("/esbuild") or member.name.endswith(
                    "/esbuild.exe"
                ):
                    f = t.extractfile(member)
                    with open(bin_path, "wb") as f_out:
                        f_out.write(f.read())
                    os.chmod(bin_path, 0o755)
                    break
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")

    with open(ver_file, "w") as f:
        f.write(ASSETS_VERSION)

    if verbose:
        Style.success("Installed esbuild to .asok/bin/")
    return bin_path


def assets_minify(root):
    """Minify all JS and CSS files in src/partials/."""
    bin_path = _esbuild_binary_path(root)
    if not os.path.exists(bin_path):
        Style.warn("Esbuild not installed. Run: asok assets --install")
        return

    Style.heading("MINIFYING ASSETS")
    count = 0

    # Target directories
    for folder in ["js", "css"]:
        base_dir = os.path.join(root, "src/partials", folder)
        if not os.path.exists(base_dir):
            continue

        for r, d, files in os.walk(base_dir):
            for f in files:
                if (
                    (f.endswith(".js") or f.endswith(".css"))
                    and not f.endswith(".min.js")
                    and not f.endswith(".min.css")
                    and not f.endswith(".build.css")
                ):
                    input_path = os.path.join(r, f)
                    output_path = input_path.rsplit(".", 1)[0] + ".min." + folder

                    print(f"  Minifying {os.path.relpath(input_path, root)}...")
                    cmd = [bin_path, input_path, "--minify", f"--outfile={output_path}"]
                    try:
                        subprocess.run(cmd, check=True, capture_output=True)
                        count += 1
                    except Exception as e:
                        Style.error(f"Failed to minify {f}: {e}")

    Style.success(f"Minified {count} asset(s)")


def _start_tailwind_watcher(root):
    """Spawn a Tailwind watcher subprocess. Returns Popen or None."""
    bin_path = _tailwind_binary_path(root)
    if not os.path.isfile(bin_path):
        print("  ⚠ Tailwind enabled but binary missing. Run: asok tailwind --install")
        return None

    input_path = os.path.join(root, "src/partials/css/base.css")
    output_path = os.path.join(root, "src/partials/css/base.build.css")

    try:
        proc = subprocess.Popen(
            [bin_path, "-i", input_path, "-o", output_path, "--watch"],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        print(f"  [tailwind] Watcher started (v{TAILWIND_VERSION})")
        return proc
    except Exception as e:
        print(f"  ⚠ Could not start Tailwind: {e}")
        return None


def get_last_mtime():
    """Get the maximum modification time among all watched files in the project."""
    max_mtime = 0
    # Include project root, src, and asok while ignoring junk
    ignore_dirs = {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "node_modules",
        "uploads",
        ".asok",
        "deployment",
    }
    watch_exts = (".py", ".html", ".json", ".css", ".js")

    for root, dirs, files in os.walk("."):
        # Prune ignored directories
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for f in files:
            if f == "base.build.css" or f.startswith("."):
                if f != ".env":  # Allow .env
                    continue

            if f.endswith(watch_exts) or f == ".env":
                try:
                    mtime = os.stat(os.path.join(root, f)).st_mtime
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
    return max_mtime


def _has_py_changed(since_mtime):
    """Check if any .py or .env file was modified after since_mtime."""
    ignore_dirs = {
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "node_modules",
        ".asok",
        "deployment",
    }

    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in ignore_dirs and not d.startswith(".")]

        for f in files:
            if f.endswith(".py") or f == ".env":
                try:
                    if os.stat(os.path.join(root, f)).st_mtime > since_mtime:
                        return True
                except OSError:
                    pass
    return False


def _find_free_port(start=8000, end=8100):
    """Find a free port starting from `start`. Returns the first available port."""

    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return None


def _start_server(port):
    """Fork a child process that runs the WSGI server on the given port.

    Returns the child PID (in the parent) or never returns (in the child).
    """
    wsgi_path = os.path.join(os.getcwd(), "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(os.getcwd(), "wsgi.pyc")

    if not os.path.isfile(wsgi_path):
        print(f"Error: WSGI entry point (wsgi.py/c) not found in {os.getcwd()}")
        return None

    pid = os.fork() if hasattr(os, "fork") else 0

    if pid == 0:  # Child (Server)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            # 1. Import wsgi.py
            spec = _ilu.spec_from_file_location("wsgi", wsgi_path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            app = mod.app

            # 2. Configure logging for DEBUG mode to show framework debug logs
            if app.config.get("DEBUG"):
                import logging

                # Use a custom handler to keep it tidy but visible
                console = logging.StreamHandler()
                console.setLevel(logging.DEBUG)
                formatter = logging.Formatter(
                    f"{Style.DIM}%(levelname)s:{Style.RESET}{Style.DIM}%(name)s:{Style.RESET} %(message)s"
                )
                console.setFormatter(formatter)
                logging.getLogger("asok.security").addHandler(console)
                logging.getLogger("asok.security").setLevel(logging.DEBUG)

        except Exception as e:
            print(f"Error loading WSGI entry point: {e}")
            traceback.print_exc()
            sys.exit(1)

        print(f"Starting Asok development server on http://127.0.0.1:{port}")
        WSGIServer.allow_reuse_address = True
        httpd = make_server("127.0.0.1", port, app, handler_class=_QuietHandler)

        def _shutdown(sig, frame):
            httpd.server_close()
            os._exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        httpd.serve_forever()

    return pid


def _kill_child(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(20):
        try:
            result = os.waitpid(pid, os.WNOHANG)
            if result[0] != 0:
                return
        except ChildProcessError:
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def run_dev(port_arg=None):
    sys.path.insert(0, os.getcwd())
    last_mtime = get_last_mtime()

    requested_port = port_arg or int(os.environ.get("ASOK_PORT", "8000"))
    port = _find_free_port(requested_port)
    if port is None:
        print(
            f"Error: No free port found between {requested_port} and {requested_port + 100}"
        )
        return
    if port != requested_port:
        Style.warn(f"Port {requested_port} is in use, using {port} instead")
        print()

    pid = _start_server(port)
    if pid is None:
        return

    tw_proc = None
    if _project_uses_tailwind(os.getcwd()):
        tw_proc = _start_tailwind_watcher(os.getcwd())

    # Parent (Watcher)
    Style.heading("DEVELOPMENT SERVER")
    print(
        f"  {Style.DIM}Reloader {Style.RESET}{Style.GREEN}●{Style.RESET}{Style.DIM} Active (PID: {os.getpid()}){Style.RESET}"
    )
    print(
        f"  {Style.DIM}URL      {Style.RESET}{Style.BOLD}http://127.0.0.1:{port}{Style.RESET}"
    )
    if tw_proc:
        print(
            f"  {Style.DIM}Tailwind {Style.RESET}{Style.GREEN}●{Style.RESET}{Style.DIM} Watching...{Style.RESET}"
        )
    print()
    try:
        while True:
            time.sleep(1)
            current_mtime = get_last_mtime()
            if current_mtime > last_mtime:
                py_changed = _has_py_changed(last_mtime)
                last_mtime = current_mtime
                if py_changed:
                    print(
                        f"  {Style.YELLOW}↻{Style.RESET} {Style.DIM}Python change, restarting...{Style.RESET}"
                    )
                    _kill_child(pid)
                    pid = _start_server(port)
                    if pid is None:
                        if tw_proc:
                            tw_proc.terminate()
                        return
                else:
                    print(
                        f"  {Style.CYAN}⚡{Style.RESET} {Style.DIM}Asset change, reloading...{Style.RESET}"
                    )
    except KeyboardInterrupt:
        _kill_child(pid)
        if tw_proc:
            tw_proc.terminate()
        sys.exit(0)


def run_preview(port_arg=None):
    """Run the app in production mode locally (no reload, no debug)."""
    sys.path.insert(0, os.getcwd())

    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()
    os.environ["DEBUG"] = "false"

    root = os.getcwd()

    # Asset minification (always run in preview if esbuild present)
    es_bin = _esbuild_binary_path(root)
    if os.path.exists(es_bin):
        assets_minify(root)

    if _project_uses_tailwind(root):
        try:
            tailwind_build(root, minify=True)
        except RuntimeError as e:
            Style.error(f"Tailwind build failed: {e}")
            return

    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")

    if not os.path.isfile(wsgi_path):
        print(f"Error: WSGI entry point (wsgi.py/c) not found in {root}")
        return

    requested_port = port_arg or int(os.environ.get("ASOK_PORT", "8000"))
    port = _find_free_port(requested_port)
    if port is None:
        print("Error: No free port found")
        return
    if port != requested_port:
        print(f"  Port {requested_port} is in use, using {port} instead")

    try:
        spec = _ilu.spec_from_file_location("wsgi", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        app = mod.app
    except Exception as e:
        print(f"Error loading 'wsgi.py': {e}")
        traceback.print_exc()
        return

    Style.heading("PREVIEW SERVER (PRODUCTION MODE)")
    print(
        f"  {Style.DIM}URL  {Style.RESET}{Style.BOLD}http://127.0.0.1:{port}{Style.RESET}"
    )
    Style.info("No auto-reload — restart manually after changes\n")

    WSGIServer.allow_reuse_address = True
    httpd = make_server("127.0.0.1", port, app, handler_class=_QuietHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


def _minify_html(html):
    """Minifies HTML content using the central safe minifier."""
    return minify_html(html)


def run_build(root, keep_source=False, output=None):
    """Generate a production-ready optimized distribution."""
    import py_compile

    Style.heading("BUILDING PRODUCTION DISTRIBUTION")

    app_name = output or "dist"
    build_root = os.path.join(root, app_name)

    if os.path.exists(build_root):
        shutil.rmtree(build_root)
    os.makedirs(build_root)

    Style.info(f"Cloning project to {Style.BOLD}{app_name}/{Style.RESET}...")

    # Exclude development-only files and folders
    ignore = shutil.ignore_patterns(
        "build",
        "dist",
        "venv",
        ".venv",
        ".asok",
        ".git",
        ".env",
        "__pycache__",
        "*.pyc",
        ".DS_Store",
        "db.sqlite3*",
        "tests",
    )

    shutil.copytree(root, build_root, ignore=ignore, dirs_exist_ok=True)

    # 1. Tailwind Build (if used)
    if _project_uses_tailwind(root):
        Style.info("Optimizing Tailwind CSS...")
        bin_path = _tailwind_binary_path(root)
        if os.path.isfile(bin_path):
            input_path = os.path.join(build_root, "src/partials/css/base.css")
            output_path = os.path.join(build_root, "src/partials/css/base.build.css")
            res = subprocess.run(
                [bin_path, "-i", input_path, "-o", output_path, "--minify"],
                cwd=root,
                capture_output=True,
            )
            if res.returncode != 0:
                Style.error(f"Tailwind build failed: {res.stderr.decode()}")
            else:
                if os.path.exists(input_path):
                    os.remove(input_path)
                Style.success("Tailwind CSS optimized and source removed.")

    # 2. Assets Minification (Universal JS/CSS)
    bin_path = _esbuild_binary_path(root)
    if os.path.isfile(bin_path):
        Style.info("Minifying JS and CSS assets...")
        target_dir = os.path.join(build_root, "src/partials")
        if os.path.exists(target_dir):
            for r, d, files in os.walk(target_dir):
                for f in files:
                    if (f.endswith(".js") or f.endswith(".css")) and not f.endswith(
                        ".build.css"
                    ):
                        path = os.path.join(r, f)
                        rel_path = os.path.relpath(path, build_root)
                        print(f"  {Style.DIM}Optimizing {rel_path}...{Style.RESET}")
                        # Minify and overwrite original
                        res = subprocess.run(
                            [
                                bin_path,
                                path,
                                "--minify",
                                f"--outfile={path}",
                                "--allow-overwrite",
                            ],
                            cwd=root,
                            capture_output=True,
                        )
                        if res.returncode != 0:
                            Style.warn(f"Minify failed for {f}: {res.stderr.decode()}")
        Style.success("Universal JS/CSS assets optimized.")
    else:
        Style.warn(
            "Asset minification skipped (esbuild not found). Run 'asok assets --install'."
        )

    Style.info("Minifying HTML templates...")
    for r, d, files in os.walk(build_root):
        for f in files:
            if f.endswith(".html"):
                path = os.path.join(r, f)
                try:
                    with open(path, "r", encoding="utf-8") as f_in:
                        content = f_in.read()
                    minified = _minify_html(content)
                    with open(path, "w", encoding="utf-8") as f_out:
                        f_out.write(minified)
                except Exception as e:
                    Style.warn(f"HTML minify failed for {f}: {e}")
    Style.success("All HTML templates minified.")

    # 4. Python Compilation
    Style.info("Compiling Python source code...")
    success_count = 0
    # Walk through EVERYTHING in the build root
    for r, d, files in os.walk(build_root):
        for f in files:
            if f.endswith(".py"):
                src = os.path.join(r, f)
                dst = src + "c"
                try:
                    # Compile to .pyc
                    py_compile.compile(src, cfile=dst, optimize=1)
                    if os.path.exists(dst) and os.path.getsize(dst) > 0:
                        success_count += 1
                        # If we don't want sources, remove the .py file
                        if not keep_source:
                            try:
                                os.remove(src)
                            except OSError:
                                pass
                except Exception as e:
                    Style.warn(f"Compile failed for {f}: {e}")

    Style.success(
        f"Compiled {success_count} Python files {'(sources removed recursively)' if not keep_source else ''}."
    )

    # Final sanity check: remove ANY remaining .py file if keep_source is False
    if not keep_source:
        for r, d, files in os.walk(build_root):
            for f in files:
                if f.endswith(".py"):
                    try:
                        os.remove(os.path.join(r, f))
                    except OSError:
                        pass

    # 5. Image Optimization (Only if enabled in config)
    if os.environ.get("IMAGE_OPTIMIZATION") == "true":
        Style.info("Optimizing project images to WebP...")
        try:
            from .utils.image import is_image, optimize_image

            optimized_count = 0
            for r, d, files in os.walk(build_root):
                for f in files:
                    if is_image(f) and not f.endswith(".webp"):
                        path = os.path.join(r, f)
                        try:
                            # Convert to webp and DELETE original
                            optimize_image(path, keep_original=False)
                            optimized_count += 1
                        except Exception:
                            pass
            Style.success(
                f"Optimized {optimized_count} images to WebP (originals removed)."
            )
        except ImportError:
            Style.warn("Image optimization skipped (Pillow not installed).")
    else:
        Style.info("Image optimization skipped (IMAGE_OPTIMIZATION not enabled).")

    # 6. Production .env
    env_prod = os.path.join(build_root, ".env.production")
    with open(env_prod, "w") as f:
        f.write("DEBUG=false\n")
        f.write("SECRET_KEY=change-me-for-production\n")
        f.write("ALLOWED_HOSTS=*\n")
        f.write("IMAGE_OPTIMIZATION=true\n")

    Style.success(
        f"Build complete! Distribution ready in: {Style.BOLD}{app_name}/{Style.RESET}"
    )
    print(f"  {Style.DIM}To preview: cd {app_name} && asok preview{Style.RESET}\n")


def run_deploy(root):
    """Generate professional, generic production deployment configurations."""
    app_name = os.path.basename(root)
    deploy_dir = os.path.join(root, "deployment")
    os.makedirs(deploy_dir, exist_ok=True)

    Style.heading("GENERATING PRODUCTION DEPLOYMENT STACK")

    # Try to grab SECRET_KEY from current .env
    secret_key = "CHANGE_ME_TO_A_LONG_SECURE_STRING"
    env_path = os.path.join(root, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.startswith("SECRET_KEY="):
                    secret_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    # 1. Gunicorn Config (Optimized)
    gunicorn_conf = f"""# Gunicorn configuration for {app_name}
import multiprocessing

bind = "127.0.0.1:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 30
keepalive = 2
accesslog = "-"
errorlog = "-"
loglevel = "info"
"""
    with open(os.path.join(deploy_dir, "gunicorn_conf.py"), "w") as f:
        f.write(gunicorn_conf)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated gunicorn_conf.py (Optimized)")

    # 2. Nginx Config (High Performance)
    nginx_conf = f"""server {{
    listen 80;
    server_name yourdomain.com; # <--- UPDATE THIS

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN";
    add_header X-XSS-Protection "1; mode=block";
    add_header X-Content-Type-Options "nosniff";

    # Gzip Compression
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml+rss image/svg+xml;

    location / {{
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}

    location /static/ {{
        alias {os.path.join(root, "src/partials/")};
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }}

    # WebSocket support (Asok native)
    location /ws/ {{
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }}
}}
"""
    with open(os.path.join(deploy_dir, "nginx.conf"), "w") as f:
        f.write(nginx_conf)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated nginx.conf (Gzip + Security)")

    # 3. SystemD Service
    service_conf = f"""[Unit]
Description=Asok Application: {app_name}
After=network.target

[Service]
User=www-data
Group=www-data
WorkingDirectory={root}
# Automatically detect virtualenv
Environment="PATH={root}/venv/bin"
Environment="SECRET_KEY={secret_key}"
Environment="DEBUG=false"
Environment="PYTHONPATH={root}"
ExecStart={root}/venv/bin/gunicorn wsgi:app -c deployment/gunicorn_conf.py

[Install]
WantedBy=multi-user.target
"""
    with open(os.path.join(deploy_dir, f"{app_name}.service"), "w") as f:
        f.write(service_conf)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated {app_name}.service (Stateless)")

    # 4. Setup Script (Automated)
    setup_sh = f"""#!/bin/bash
# Universal Asok Setup Script for Ubuntu/Debian
set -e

echo "--------------------------------------------------------"
echo "  ASOK PRODUCTION SETUP: {app_name}"
echo "--------------------------------------------------------"

# 1. System Dependencies
echo "[1/5] Installing system dependencies..."
sudo apt update
sudo apt install -y nginx python3-pip python3-venv

# 2. Virtual Environment
echo "[2/5] Setting up virtual environment..."
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install gunicorn

# Attempt to install requirements if they exist
if [ -f "requirements.txt" ]; then
    ./venv/bin/pip install -r requirements.txt
fi

# 3. Permissions (Crucial for SQLite/Uploads)
echo "[3/5] Setting up permissions for www-data..."
sudo chown -R $USER:www-data .
sudo chmod -R 775 src/partials/uploads || true
if [ -f "db.sqlite3" ]; then
    sudo chown www-data:www-data db.sqlite3
    sudo chmod 664 db.sqlite3
fi

# 4. SystemD Config
echo "[4/5] Configuring SystemD service..."
sudo cp deployment/{app_name}.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable {app_name}
sudo systemctl restart {app_name}

# 5. Nginx Config
echo "[5/5] Configuring Nginx reverse-proxy..."
sudo cp deployment/nginx.conf /etc/nginx/sites-available/{app_name}
sudo ln -sf /etc/nginx/sites-available/{app_name} /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

echo "--------------------------------------------------------"
echo "  SUCCESS! YOUR APP IS NOW LIVE."
echo "--------------------------------------------------------"
echo "Next steps:"
echo "1. Update yourdomain.com in /etc/nginx/sites-available/{app_name}"
echo "2. Run: sudo apt install certbot python3-certbot-nginx"
echo "3. Run: sudo certbot --nginx -d yourdomain.com"
echo "--------------------------------------------------------"
"""
    with open(os.path.join(deploy_dir, "setup.sh"), "w") as f:
        f.write(setup_sh)
    os.chmod(os.path.join(deploy_dir, "setup.sh"), 0o755)
    print(f"  {Style.GREEN}✓{Style.RESET} Generated setup.sh (Automated)")

    Style.success("\nDeployment stack generated successfully in: deployment/")
    print(
        f"  To deploy, copy the folder to your server and run: {Style.BOLD}sudo ./deployment/setup.sh{Style.RESET}\n"
    )


def run_migrate():
    """Auto-migrate: detect new columns in models and ALTER TABLE to add them."""
    sys.path.insert(0, os.getcwd())

    # Load .env
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    Style.heading("MIGRATIONS")
    model_dir = os.path.join(os.getcwd(), "src/models")
    if not os.path.isdir(model_dir):
        Style.error("No src/models/ directory found.")
        return

    for filename in sorted(os.listdir(model_dir)):
        if filename.endswith(".py") and not filename.startswith("__"):
            filepath = os.path.join(model_dir, filename)
            spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)

    db_path = Model._db_path
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Collect all changes grouped by table
    all_changes = {}

    for name, model_cls in MODELS_REGISTRY.items():
        model_cls.create_table()
        table = model_cls._table

        existing_cols = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        existing = set(existing_cols)
        model_fields = set(model_cls._fields.keys())

        table_changes = {"added": [], "removed": [], "failed": []}

        # Add new columns
        for field_name, field_obj in model_cls._fields.items():
            if field_name not in existing:
                default = ""
                if field_obj.default is not None:
                    if isinstance(field_obj.default, bool):
                        default = f" DEFAULT {str(field_obj.default).lower()}"
                    elif isinstance(field_obj.default, (int, float)):
                        default = f" DEFAULT {field_obj.default}"
                    else:
                        default = f" DEFAULT '{field_obj.default}'"
                sql = f"ALTER TABLE {table} ADD COLUMN {field_name} {field_obj.sql_type}{default}"
                try:
                    conn.execute(sql)
                    table_changes["added"].append((field_name, field_obj.sql_type))
                except sqlite3.OperationalError as e:
                    table_changes["failed"].append((field_name, str(e)))

        # Drop removed columns (SQLite 3.35+ supports DROP COLUMN)
        removed = [c for c in existing_cols if c != "id" and c not in model_fields]
        for col in removed:
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
                table_changes["removed"].append(col)
            except sqlite3.OperationalError:
                # Silently skip if DROP COLUMN not supported
                pass

        # Only track tables with actual changes
        if (
            table_changes["added"]
            or table_changes["removed"]
            or table_changes["failed"]
        ):
            all_changes[table] = table_changes

    conn.commit()
    conn.close()

    # Display changes grouped by table
    total_changes = 0
    for table in sorted(all_changes.keys()):
        changes = all_changes[table]
        if changes["added"] or changes["removed"] or changes["failed"]:
            print(f"\n  {Style.BOLD}{table}{Style.RESET}")

            for field_name, sql_type in changes["added"]:
                print(
                    f"    {Style.GREEN}+{Style.RESET} {field_name} {Style.DIM}({sql_type}){Style.RESET}"
                )
                total_changes += 1

            for col in changes["removed"]:
                print(f"    {Style.RED}−{Style.RESET} {col}")
                total_changes += 1

            for field_name, error in changes["failed"]:
                print(
                    f"    {Style.YELLOW}!{Style.RESET} {field_name} {Style.DIM}(failed: {error}){Style.RESET}"
                )

    print()  # Empty line before summary
    if total_changes:
        Style.success(
            f"Applied {total_changes} change(s) across {len(all_changes)} table(s)."
        )
    else:
        Style.info("Database schema is up to date.")


def run_seed():
    Style.heading("SEEDING DATA")
    sys.path.insert(0, os.getcwd())
    seed_path = os.path.join(os.getcwd(), "src", "seeds.py")
    if not os.path.isfile(seed_path):
        Style.warn("No src/seeds.py found. Create one with a run() function.")
        return

    model_dir = os.path.join(os.getcwd(), "src/models")
    if os.path.isdir(model_dir):
        for filename in sorted(os.listdir(model_dir)):
            if filename.endswith(".py") and not filename.startswith("__"):
                filepath = os.path.join(model_dir, filename)
                spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, Model)
                        and attr is not Model
                    ):
                        attr.create_table()

    spec = _ilu.spec_from_file_location("seeds", seed_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if hasattr(mod, "run"):
        mod.run()
        Style.success("Seeding complete.")
    else:
        Style.error("src/seeds.py must define a run() function.")


def make_model(name):
    """Generate a model file in src/models/."""
    os.makedirs("src/models", exist_ok=True)
    filename = f"src/models/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return
    content = f"""\
from asok import Model, Field

class {name.capitalize()}(Model):
    name = Field.String()
    created_at = Field.CreatedAt()
    updated_at = Field.UpdatedAt()
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"File created: {Style.BOLD}{filename}{Style.RESET}")


def make_middleware(name):
    """Generate a middleware file in src/middlewares/."""
    os.makedirs("src/middlewares", exist_ok=True)
    filename = f"src/middlewares/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return
    content = """\
def handle(request, next):
    # Pre-processing
    response = next(request)
    # Post-processing
    return response
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"File created: {Style.BOLD}{filename}{Style.RESET}")


def make_page(name):
    """Generate a page directory with page.py and page.html."""
    page_dir = f"src/pages/{name}"
    os.makedirs(page_dir, exist_ok=True)

    py_path = os.path.join(page_dir, "page.py")
    if not os.path.exists(py_path):
        with open(py_path, "w", encoding="utf-8") as f:
            f.write("""\
from asok import Request

def render(request: Request):
    return request.html('page.html')
""")

    html_path = os.path.join(page_dir, "page.html")
    if not os.path.exists(html_path):
        title = name.replace("/", " ").replace("-", " ").title()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(f"""\
{{% extends "html/base.html" %}}

{{% block title %}}{title}{{% endblock %}}

{{% block main %}}
<div class="container page-header">
    <h1>{title}</h1>
</div>
{{% endblock %}}
""")

    Style.success(f"Page created: {Style.BOLD}{page_dir}/...{Style.RESET}")


def make_component(name: str) -> None:
    """Generate a high-level UI component in src/components/."""
    os.makedirs("src/components", exist_ok=True)
    filename = f"src/components/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return

    class_name = "".join(x.capitalize() for x in name.replace("-", "_").split("_"))
    content = f"""\
from asok.component import Component

class {class_name}(Component):
    \"\"\"Reusable UI component for {name}.\"\"\"

    def render(self) -> str:
        return self.html(\"\"\"
            <div class="{name.lower()}">
                <!-- Component Content -->
                <p>{name.capitalize()} Component</p>
            </div>
        \"\"\")
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"Component created: {Style.BOLD}{filename}{Style.RESET}")


def run_routes():
    """List all routes by walking src/pages/."""
    Style.heading("ROUTES")
    pages_dir = os.path.join(os.getcwd(), "src/pages")
    if not os.path.isdir(pages_dir):
        Style.error("No src/pages/ directory found.")
        return
    routes = []
    for root, _, files in os.walk(pages_dir):
        if "page.py" in files or "page.html" in files:
            rel = os.path.relpath(root, pages_dir).replace(os.sep, "/")
            url = "/" if rel == "." else "/" + rel
            handler = "page.py" if "page.py" in files else "page.html"
            routes.append((url, handler))
    routes.sort()
    if not routes:
        Style.info("No routes found.")
        return

    u_width = max(len(u) for u, _ in routes)
    print(f"  {Style.BOLD}{Style.DIM}{'URL'.ljust(u_width)}   {'HANDLER'}{Style.RESET}")
    print(f"  {Style.DIM}{'-' * u_width}   {'-' * 15}{Style.RESET}")
    for url, handler in routes:
        h_color = Style.GREEN if handler.endswith(".py") else Style.CYAN
        print(
            f"  {Style.BOLD}{url.ljust(u_width)}{Style.RESET}   {h_color}{handler}{Style.RESET}"
        )
    print()


def run_shell():
    """Interactive Python shell with all models pre-imported."""
    banner = f"{Style.BOLD}{Style.CYAN}Asok Shell{Style.RESET} {Style.DIM}(Interactive Python){Style.RESET}"
    print(f"\n{banner}")
    Style.info("All models and 'app' instance pre-imported.\n")
    sys.path.insert(0, os.getcwd())
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

    model_dir = os.path.join(os.getcwd(), "src/models")
    ns = {"Model": Model}

    # Load wsgi.py or wsgi.pyc to get 'app' instance
    wsgi_path = os.path.join(os.getcwd(), "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(os.getcwd(), "wsgi.pyc")

    if os.path.isfile(wsgi_path):
        try:
            spec = _ilu.spec_from_file_location("_wsgi", wsgi_path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "app"):
                ns["app"] = mod.app
        except Exception as e:
            Style.warn(f"Could not load 'app' from WSGI entry point: {e}")

    if os.path.isdir(model_dir):
        for filename in sorted(os.listdir(model_dir)):
            if filename.endswith(".py") and not filename.startswith("__"):
                filepath = os.path.join(model_dir, filename)
                spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
    ns.update(MODELS_REGISTRY)
    banner = (
        f"Asok shell — models loaded: {', '.join(MODELS_REGISTRY) or '(none)'}\n"
        f"Python {sys.version.split()[0]}"
    )
    try:
        import readline  # noqa: F401
    except ImportError:
        pass

    code.interact(banner=banner, local=ns)


def run_test(path=None):
    """Discover and run tests in tests/ directory."""
    sys.path.insert(0, os.getcwd())
    import unittest

    target = path or "tests"
    if not os.path.isdir(target):
        print(f"No '{target}/' directory found.")
        return
    loader = unittest.TestLoader()
    suite = loader.discover(target)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def run_createsuperuser(email=None, password=None):
    root = _find_project_root()
    if not root:
        print("Error: Not inside an Asok project (no wsgi.py/c found).")
        sys.exit(1)
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    # Load wsgi entry point to ensure models are registered
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")

    spec = _ilu.spec_from_file_location("_wsgi", wsgi_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    from .orm import MODELS_REGISTRY

    User = MODELS_REGISTRY.get(getattr(mod, "app").config.get("AUTH_MODEL", "User"))
    if not User:
        Style.error("User model not found.")
        sys.exit(1)

    Style.heading("CREATE SUPERUSER")
    if not email:
        email = input(f"  {Style.BOLD}Enter your email address:{Style.RESET} ").strip()
    if not password:
        password = getpass.getpass(f"  {Style.BOLD}Enter your password:{Style.RESET} ")
        confirm = getpass.getpass(f"  {Style.BOLD}Confirm your password:{Style.RESET} ")
        if password != confirm:
            Style.error("Passwords don't match.")
            sys.exit(1)
    if not email or not password:
        Style.error("Email and password required.")
        sys.exit(1)

    existing = User.find(email=email)
    if existing:
        existing.password = password
        existing.is_admin = True
        existing.save()
        user = existing
        Style.success(
            f"Updated existing user '{Style.BOLD}{email}{Style.RESET}' as admin."
        )
    else:
        user = User.create(_trust=True, email=email, password=password, is_admin=True)
        Style.success(f"Superuser '{Style.BOLD}{email}{Style.RESET}' created.")

    # Ensure the 'admin' role exists with full permissions and attach it
    Role = MODELS_REGISTRY.get("Role")
    if Role:
        try:
            with User._get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS role_user ("
                    "role_id INTEGER NOT NULL, "
                    "user_id INTEGER NOT NULL, "
                    "PRIMARY KEY (role_id, user_id))"
                )
            admin_role = Role.find(name="admin")
            if not admin_role:
                admin_role = Role.create(
                    name="admin", label="Administrator", permissions="*"
                )
                Style.success("Created 'admin' role with full permissions.")
            with User._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO role_user (role_id, user_id) VALUES (?, ?)",
                    (admin_role.id, user.id),
                )
        except Exception as e:
            print(f"  ⚠ Could not attach admin role: {e}")


def print_help():
    """Custom professional help display for Asok."""
    print(
        f"\n{Style.BOLD}{Style.CYAN}ASOK FRAMEWORK{Style.RESET} {Style.DIM}v1.0.0{Style.RESET}"
    )
    print("Minimalist Python Web Framework (Zero Dependencies)\n")

    print(f"{Style.BOLD}{Style.BLUE}Usage:{Style.RESET}")
    print("  asok <command> [options]\n")

    groups = {
        "Scaffolding": [
            ("create", "Create a new Asok project"),
            ("make page", "Create a new page (py + html)"),
            ("make component", "Create a new reusable UI component"),
            ("make model", "Create a new database model"),
            ("make middleware", "Create a new middleware"),
        ],
        "Development": [
            ("dev", "Start the development server with hot-reload"),
            ("preview", "Start the production-ready server locally"),
            ("shell", "Open an interactive Python shell with app context"),
            ("routes", "Display all registered routes"),
            ("test", "Run the project's test suite"),
        ],
        "Database": [
            ("migrate", "Apply pending database migrations"),
            ("seed", "Run database seeders"),
            ("createsuperuser", "Create or update an administrative user"),
        ],
        "Tools": [
            ("tailwind", "Manage Tailwind CSS (install/build/enable)"),
            ("admin", "Manage Admin interface (enable)"),
            ("image", "Manage Image Optimization (install/enable/optimize)"),
            ("assets", "Manage JS/CSS assets (install/minify)"),
            (
                "deploy",
                "Generate production deployment configs (Gunicorn/Nginx/SystemD)",
            ),
            ("build", "Generate a production-ready optimized build folder"),
        ],
    }

    for group, commands in groups.items():
        print(f"{Style.BOLD}{Style.BLUE}{group}:{Style.RESET}")
        for cmd, help_text in commands:
            print(
                f"  {Style.GREEN}{cmd:<15}{Style.RESET} {Style.DIM}{help_text}{Style.RESET}"
            )
        print()


def main() -> None:
    """Terminal entry point for the 'asok' CLI.

    Dispatches commands to their respective handlers, manages project scaffolding,
    Tailwind setup, migrations, and development server execution.
    """
    parser = argparse.ArgumentParser(description="Asok Framework CLI", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    # Command definitions
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("name")
    create_parser.add_argument("--tailwind", action="store_true", default=None)
    create_parser.add_argument("--admin", action="store_true", default=None)
    create_parser.add_argument("--image", action="store_true", default=None)

    cs_parser = subparsers.add_parser("createsuperuser")
    cs_parser.add_argument("--email", default=None)
    cs_parser.add_argument("--password", default=None)

    tw_parser = subparsers.add_parser("tailwind")
    tw_group = tw_parser.add_mutually_exclusive_group()
    tw_group.add_argument("--install", action="store_true")
    tw_group.add_argument("--build", action="store_true")
    tw_group.add_argument("--enable", action="store_true")
    tw_parser.add_argument("--minify", action="store_true")

    admin_parser = subparsers.add_parser("admin")
    admin_parser.add_argument("--enable", action="store_true")

    image_parser = subparsers.add_parser("image")
    image_parser.add_argument("--install", action="store_true")
    image_parser.add_argument("--enable", action="store_true")
    image_parser.add_argument("--optimize", action="store_true")
    image_parser.add_argument("--delete-originals", action="store_true")

    assets_parser = subparsers.add_parser("assets")
    assets_parser.add_argument("--install", action="store_true")
    assets_parser.add_argument("--minify", action="store_true")

    subparsers.add_parser("deploy")
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep .py source files along with bytecode",
    )
    build_parser.add_argument(
        "--output", "-o", default=None, help="Output directory name"
    )

    subparsers.add_parser("dev").add_argument("-p", "--port", type=int, default=None)
    subparsers.add_parser("preview").add_argument(
        "-p", "--port", type=int, default=None
    )
    subparsers.add_parser("migrate")
    subparsers.add_parser("seed")
    subparsers.add_parser("routes")
    subparsers.add_parser("shell")
    subparsers.add_parser("test").add_argument("path", nargs="?", default=None)

    make_parser = subparsers.add_parser("make")
    make_parser.add_argument(
        "type", choices=["model", "middleware", "page", "component"]
    )
    make_parser.add_argument("name")

    # Catch empty args, help or version request
    if len(sys.argv) == 1 or "-h" in sys.argv or "--help" in sys.argv:
        print_help()
        return

    if "-v" in sys.argv or "--version" in sys.argv:
        print(f"Asok Framework v{__version__}")
        return

    args = parser.parse_args()

    if args.command == "create":
        scaffold(args.name, tailwind=args.tailwind, admin=args.admin, image=args.image)
    elif args.command == "createsuperuser":
        run_createsuperuser(args.email, args.password)
    elif args.command == "tailwind":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return

        try:
            if args.enable:
                tailwind_enable(root)
            elif args.install:
                tailwind_install(root, verbose=True)
            elif args.build:
                if not _project_uses_tailwind(root):
                    Style.warn("This project doesn't use Tailwind.")
                    print("  To enable it, run: asok tailwind --enable")
                    return
                tailwind_build(root, minify=args.minify)
            else:
                tw_parser.print_help()
        except RuntimeError as e:
            Style.error(str(e))
            sys.exit(1)
    elif args.command == "admin":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.enable:
            admin_enable(root)
        else:
            admin_parser.print_help()
    elif args.command == "image":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.enable:
            image_enable(root)
        elif args.install:
            image_install(root)
        elif args.optimize:
            image_optimize_all(root, delete_originals=args.delete_originals)
        else:
            image_parser.print_help()
    elif args.command == "assets":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.install:
            assets_install(root)
        elif args.minify:
            assets_minify(root)
        else:
            assets_parser.print_help()
    elif args.command == "deploy":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        run_deploy(root)
    elif args.command == "build":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        run_build(root, keep_source=args.keep_source, output=args.output)
    elif args.command == "dev":
        run_dev(args.port)
    elif args.command == "preview":
        run_preview(args.port)
    elif args.command == "migrate":
        run_migrate()
    elif args.command == "seed":
        run_seed()
    elif args.command == "routes":
        run_routes()
    elif args.command == "shell":
        run_shell()
    elif args.command == "test":
        run_test(args.path)
    elif args.command == "make":
        if args.type == "model":
            make_model(args.name)
        elif args.type == "middleware":
            make_middleware(args.name)
        elif args.type == "page":
            make_page(args.name)
        elif args.type == "component":
            make_component(args.name)
    else:
        print_help()


if __name__ == "__main__":
    main()
