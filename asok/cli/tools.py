from __future__ import annotations

import os
import platform as _p
import subprocess
import tarfile
import urllib.request
import zipfile
from io import BytesIO

from .style import Style

TAILWIND_VERSION = "4.3.0"
IMAGE_VERSION = "1.6.0"
ASSETS_VERSION = "0.28.0"

_GRAPHIQL_STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "api", "static", "graphiql"
)
_GRAPHIQL_ASSETS = {
    "react.min.js":         "https://unpkg.com/react@18.3.1/umd/react.production.min.js",
    "react-dom.min.js":     "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js",
    "graphiql.min.js":      "https://unpkg.com/graphiql@3.0.6/graphiql.min.js",
    "graphiql.min.css":     "https://unpkg.com/graphiql@3.0.6/graphiql.min.css",
}

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


def _log_info(msg: str, verbose: bool) -> None:
    if verbose:
        print(msg)


def _tailwind_platform_suffix() -> str:
    key = (_p.system(), _p.machine())
    suffix = _TAILWIND_PLATFORMS.get(key)
    if not suffix:
        raise RuntimeError(
            f"No Tailwind binary for {key[0]}/{key[1]}. "
            f"Install manually from https://github.com/tailwindlabs/tailwindcss/releases"
        )
    return suffix


def _tailwind_binary_path(root: str) -> str:
    suffix = _tailwind_platform_suffix()
    name = "tailwindcss.exe" if suffix.endswith(".exe") else "tailwindcss"
    return os.path.join(root, ".asok", "bin", name)


def _tailwind_version_file(root: str) -> str:
    return os.path.join(root, ".asok", "bin", "version.txt")


def _read_current_tailwind_version(version_path: str) -> str | None:
    if os.path.isfile(version_path):
        try:
            with open(version_path) as f:
                return f.read().strip()
        except OSError:
            pass
    return None


def _download_tailwind(url: str, bin_path: str, suffix: str) -> None:
    if not url.startswith("https://"):
        raise RuntimeError("Security error: only HTTPS downloads are allowed")
    try:
        urllib.request.urlretrieve(url, bin_path)
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")
    if not suffix.endswith(".exe"):
        os.chmod(bin_path, 0o755)


def tailwind_install(root: str, verbose: bool = True) -> str:
    """Download the pinned Tailwind binary into .asok/bin/ if missing or outdated."""
    suffix = _tailwind_platform_suffix()
    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)

    bin_path = _tailwind_binary_path(root)
    version_path = _tailwind_version_file(root)

    current_version = _read_current_tailwind_version(version_path)
    if os.path.isfile(bin_path) and current_version == TAILWIND_VERSION:
        _log_info(f"  Tailwind v{TAILWIND_VERSION} already installed", verbose)
        return bin_path

    url = (
        f"https://github.com/tailwindlabs/tailwindcss/releases/download/"
        f"v{TAILWIND_VERSION}/tailwindcss-{suffix}"
    )
    _log_info(f"  Downloading Tailwind v{TAILWIND_VERSION} ({suffix})...", verbose)

    _download_tailwind(url, bin_path, suffix)

    with open(version_path, "w") as f:
        f.write(TAILWIND_VERSION)

    if verbose:
        Style.success("Installed to .asok/bin/")
    return bin_path


def _validate_tailwind_paths(root: str, bin_path: str, input_path: str, output_path: str) -> None:
    if not os.path.isfile(bin_path):
        raise RuntimeError("Tailwind not installed. Run: asok tailwind --install")
    expected_dir = os.path.join(root, ".asok", "bin")
    if not os.path.abspath(bin_path).startswith(os.path.abspath(expected_dir)):
        raise RuntimeError("Security error: invalid binary path")
    if not os.path.abspath(input_path).startswith(os.path.abspath(root)):
        raise RuntimeError("Security error: invalid input path")
    if not os.path.abspath(output_path).startswith(os.path.abspath(root)):
        raise RuntimeError("Security error: invalid output path")


def tailwind_build(root: str, minify: bool = False) -> None:
    """Run a one-shot Tailwind build."""
    bin_path = _tailwind_binary_path(root)
    input_path = os.path.join(root, "src/partials/css/base.css")
    output_path = os.path.join(root, "src/partials/css/base.build.css")

    _validate_tailwind_paths(root, bin_path, input_path, output_path)

    cmd = [bin_path, "-i", input_path, "-o", output_path]
    if minify:
        cmd.append("--minify")

    print(f"  Building CSS{' (minified)' if minify else ''}...")
    result = subprocess.run(cmd, cwd=root)
    if result.returncode != 0:
        Style.error("Tailwind build failed")
        raise RuntimeError("Tailwind build failed")
    Style.success(f"Built {os.path.relpath(output_path, root)}")


def tailwind_enable(root: str) -> None:
    """Enable Tailwind CSS in an existing project."""
    Style.heading("ENABLING TAILWIND CSS")

    css_path = os.path.join(root, "src/partials/css/base.css")
    os.makedirs(os.path.dirname(css_path), exist_ok=True)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write('@import "tailwindcss";\n')
    Style.success("Reset src/partials/css/base.css with Tailwind import")

    for base_name in ("base.html", "base.asok"):
        base_path = os.path.join(root, "src/partials/html", base_name)
        if os.path.isfile(base_path):
            with open(base_path, "r", encoding="utf-8") as f:
                content = f.read()

            old_link = "href=\"{{ static('css/base.css') }}\""
            new_link = "href=\"{{ static('css/base.build.css') }}\""

            if old_link in content:
                content = content.replace(old_link, new_link)
                with open(base_path, "w", encoding="utf-8") as f:
                    f.write(content)
                Style.success(f"Updated {base_name} to use compiled CSS")

    tailwind_install(root, verbose=True)
    tailwind_build(root, minify=False)
    Style.success("Tailwind CSS is now enabled and ready!")


def _insert_admin_init(lines: list[str]) -> list[str]:
    new_lines = []
    inserted = False
    for line in lines:
        new_lines.append(line)
        if "app = Asok()" in line and not inserted:
            new_lines.append("Admin(app)\n")
            inserted = True
    return new_lines


def _add_admin_lines(lines: list[str]) -> list[str]:
    has_import = any("from asok.admin import Admin" in line for line in lines)
    has_init = any("Admin(app)" in line for line in lines)

    if not has_import:
        lines.insert(0, "from asok.admin import Admin\n")
    if not has_init:
        lines = _insert_admin_init(lines)
    return lines


def _update_wsgi_for_admin(wsgi_path: str) -> None:
    if not os.path.isfile(wsgi_path):
        return
    with open(wsgi_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    lines = _add_admin_lines(lines)

    with open(wsgi_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    Style.success("Updated wsgi.py to include Admin(app)")


def _ensure_user_model(root: str) -> None:
    user_model = os.path.join(root, "src/models/user.py")
    if os.path.isfile(user_model):
        return
    os.makedirs(os.path.dirname(user_model), exist_ok=True)
    with open(user_model, "w", encoding="utf-8") as f:
        f.write("""from asok import Field, Model

class User(Model):
    email = Field.String(unique=True, nullable=False)
    password = Field.Password()
    name = Field.String()
    is_admin = Field.Boolean(default=False)
    totp_secret = Field.String(nullable=True, hidden=True)
    totp_enabled = Field.Boolean(default=False)
    backup_codes = Field.String(nullable=True, hidden=True)
    created_at = Field.CreatedAt()
""")
    Style.success("Created default User model in src/models/user.py")


def _ensure_role_model(root: str) -> None:
    role_model = os.path.join(root, "src/models/role.py")
    if os.path.isfile(role_model):
        return
    os.makedirs(os.path.dirname(role_model), exist_ok=True)
    with open(role_model, "w", encoding="utf-8") as f:
        f.write("""from asok import Field, Model


class Role(Model):
    name = Field.String(unique=True, nullable=False)
    label = Field.String()
    permissions = Field.String(default="")
    created_at = Field.CreatedAt()

    def __str__(self):
        return self.label or self.name
""")
    Style.success("Created default Role model in src/models/role.py")


def _ensure_log_model(root: str) -> None:
    log_model = os.path.join(root, "src/models/admin_log.py")
    if os.path.isfile(log_model):
        return
    os.makedirs(os.path.dirname(log_model), exist_ok=True)
    with open(log_model, "w", encoding="utf-8") as f:
        f.write("""from asok import Field, Model


class AdminLog(Model):
    user_id = Field.Integer(nullable=True)
    action = Field.String(nullable=False)
    entity = Field.String(nullable=False)
    entity_id = Field.Integer(nullable=True)
    changes = Field.String()
    created_at = Field.CreatedAt()

    class Admin:
        label = "Audit logs"
        slug = "logs"
        list_display = ["id", "created_at", "user_id", "action", "entity", "entity_id"]
        search_fields = ["action", "entity", "changes"]
        list_filter = ["action", "entity"]
        can_add = False
        can_edit = False
        can_delete = False
""")
    Style.success("Created default AdminLog model in src/models/admin_log.py")


def admin_enable(root: str) -> None:
    """Enable Admin interface in an existing project."""
    Style.heading("ENABLING ADMIN INTERFACE")
    _update_wsgi_for_admin(os.path.join(root, "wsgi.py"))
    _ensure_user_model(root)
    _ensure_role_model(root)
    _ensure_log_model(root)

    Style.info("Next steps:")
    print("  1. Run 'asok make migration add_admin' to detect new tables")
    print("  2. Run 'asok migrate' to apply them")
    print("  3. Run 'asok createsuperuser' to create your first account")
    print("  4. Visit /admin in your browser\n")


def _image_binary_path(root: str) -> str:
    suffix = _tailwind_platform_suffix()
    name = "cwebp.exe" if suffix.endswith(".exe") else "cwebp"
    return os.path.join(root, ".asok", "bin", name)


def _is_image_installed(bin_path: str, ver_file: str) -> bool:
    if not (os.path.exists(bin_path) and os.path.exists(ver_file)):
        return False
    try:
        with open(ver_file) as f:
            return f.read().strip() == IMAGE_VERSION
    except OSError:
        return False


def _extract_webp_zip(buf: BytesIO, bin_path: str) -> None:
    with zipfile.ZipFile(buf) as z:
        for name in z.namelist():
            if name.endswith("cwebp.exe"):
                with open(bin_path, "wb") as f:
                    f.write(z.read(name))
                break


def _extract_webp_tar(buf: BytesIO, bin_path: str) -> None:
    with tarfile.open(fileobj=buf, mode="r:gz") as t:
        for member in t.getmembers():
            if member.name.endswith("/cwebp"):
                f = t.extractfile(member)
                if f:
                    with open(bin_path, "wb") as f_out:
                        f_out.write(f.read())
                    os.chmod(bin_path, 0o755)
                break


def _download_webp_buf(url: str) -> BytesIO:
    try:
        resp = urllib.request.urlopen(url)
        return BytesIO(resp.read())
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")


def _extract_webp_archive(ext: str, buf: BytesIO, bin_path: str) -> None:
    if ext == "zip":
        _extract_webp_zip(buf, bin_path)
    else:
        _extract_webp_tar(buf, bin_path)


def image_install(root: str, verbose: bool = True) -> str:
    """Download and extract libwebp cwebp binary."""
    key = (_p.system(), _p.machine())
    os_suffix = _IMAGE_PLATFORMS.get(key)
    if not os_suffix:
        raise RuntimeError(f"No libwebp binary for {key}")

    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bin_path = _image_binary_path(root)
    ver_file = os.path.join(bin_dir, "image_version.txt")

    if _is_image_installed(bin_path, ver_file):
        _log_info(f"  libwebp v{IMAGE_VERSION} already installed", verbose)
        return bin_path

    ext = "zip" if "windows" in os_suffix else "tar.gz"
    url = f"https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-{IMAGE_VERSION}-{os_suffix}.{ext}"

    _log_info(f"  Downloading libwebp v{IMAGE_VERSION} ({os_suffix})...", verbose)

    buf = _download_webp_buf(url)
    _extract_webp_archive(ext, buf, bin_path)

    with open(ver_file, "w") as f:
        f.write(IMAGE_VERSION)

    if verbose:
        Style.success("Installed cwebp to .asok/bin/")
    return bin_path


def image_enable(root: str) -> None:
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


def _try_delete_original(path: str, delete_originals: bool) -> None:
    if delete_originals:
        try:
            os.remove(path)
        except Exception:
            pass


def _optimize_single_file(path: str, root: str, delete_originals: bool) -> bool:
    from ..utils.image import is_image, optimize_image
    if not is_image(path):
        return False
    if os.path.exists(path + ".webp"):
        _try_delete_original(path, delete_originals)
        return False

    print(f"  Optimizing {os.path.relpath(path, root)}...")
    return bool(optimize_image(
        path, root=root, keep_original=not delete_originals
    ))


def image_optimize_all(root: str, delete_originals: bool = False) -> None:
    """Scan and optimize all existing images in the project."""
    Style.heading("OPTIMIZING EXISTING IMAGES")
    count = 0
    base_dir = os.path.join(root, "src/partials")
    if not os.path.exists(base_dir):
        Style.success(f"Optimized {count} image(s)")
        return

    for r, _, files in os.walk(base_dir):
        for f in files:
            path = os.path.join(r, f)
            if _optimize_single_file(path, root, delete_originals):
                count += 1

    Style.success(f"Optimized {count} image(s)")


def _esbuild_binary_path(root: str) -> str:
    suffix = _tailwind_platform_suffix()
    name = "esbuild.exe" if suffix.endswith(".exe") else "esbuild"
    return os.path.join(root, ".asok", "bin", name)


def _is_assets_installed(bin_path: str, ver_file: str) -> bool:
    if not (os.path.exists(bin_path) and os.path.exists(ver_file)):
        return False
    try:
        with open(ver_file) as f:
            return f.read().strip() == ASSETS_VERSION
    except OSError:
        return False


def _extract_esbuild(buf: BytesIO, bin_path: str) -> None:
    with tarfile.open(fileobj=buf, mode="r:gz") as t:
        for member in t.getmembers():
            if member.name.endswith("/esbuild") or member.name.endswith("/esbuild.exe"):
                f = t.extractfile(member)
                if f:
                    with open(bin_path, "wb") as f_out:
                        f_out.write(f.read())
                    os.chmod(bin_path, 0o755)
                break


def assets_install(root: str, verbose: bool = True) -> str:
    """Download and extract esbuild binary from npm registry."""
    key = (_p.system(), _p.machine())
    npm_pkg = _ESBUILD_PLATFORMS.get(key)
    if not npm_pkg:
        raise RuntimeError(f"No esbuild binary for {key}")

    bin_dir = os.path.join(root, ".asok", "bin")
    os.makedirs(bin_dir, exist_ok=True)
    bin_path = _esbuild_binary_path(root)
    ver_file = os.path.join(bin_dir, "assets_version.txt")

    if _is_assets_installed(bin_path, ver_file):
        _log_info(f"  Esbuild v{ASSETS_VERSION} already installed", verbose)
        return bin_path

    url = f"https://registry.npmjs.org/@esbuild/{npm_pkg}/-/{npm_pkg}-{ASSETS_VERSION}.tgz"
    _log_info(f"  Downloading Esbuild v{ASSETS_VERSION} ({npm_pkg})...", verbose)

    try:
        resp = urllib.request.urlopen(url)
        buf = BytesIO(resp.read())
        _extract_esbuild(buf, bin_path)
    except Exception as e:
        raise RuntimeError(f"Download failed from {url}: {e}")

    with open(ver_file, "w") as f:
        f.write(ASSETS_VERSION)

    if verbose:
        Style.success("Installed esbuild to .asok/bin/")
    return bin_path


def _has_minify_ext(f: str) -> bool:
    return f.endswith(".js") or f.endswith(".css")


def _is_already_minified(f: str) -> bool:
    return f.endswith(".min.js") or f.endswith(".min.css") or f.endswith(".build.css")


def _should_minify_file(f: str) -> bool:
    return _has_minify_ext(f) and not _is_already_minified(f)


def _minify_single_file(bin_path: str, r: str, f: str, folder: str, root: str) -> bool:
    input_path = os.path.join(r, f)
    output_path = input_path.rsplit(".", 1)[0] + ".min." + folder

    print(f"  Minifying {os.path.relpath(input_path, root)}...")
    cmd = [bin_path, input_path, "--minify", f"--outfile={output_path}"]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except Exception as e:
        Style.error(f"Failed to minify {f}: {e}")
        return False


def _minify_files_in_dir(r: str, files: list[str], folder: str, bin_path: str, root: str) -> int:
    count = 0
    for f in files:
        if _should_minify_file(f) and _minify_single_file(bin_path, r, f, folder, root):
            count += 1
    return count


def _minify_folder(folder: str, bin_path: str, root: str) -> int:
    base_dir = os.path.join(root, "src/partials", folder)
    if not os.path.exists(base_dir):
        return 0
    count = 0
    for r, _, files in os.walk(base_dir):
        count += _minify_files_in_dir(r, files, folder, bin_path, root)
    return count


def assets_minify(root: str) -> None:
    """Minify all JS and CSS files in src/partials/."""
    bin_path = _esbuild_binary_path(root)
    if not os.path.exists(bin_path):
        Style.warn("Esbuild not installed. Run: asok assets --install")
        return

    Style.heading("MINIFYING ASSETS")
    count = sum(_minify_folder(folder, bin_path, root) for folder in ["js", "css"])
    Style.success(f"Minified {count} asset(s)")


def graphql_assets_installed() -> bool:
    """Return True if all GraphiQL assets are present locally."""
    return all(
        os.path.isfile(os.path.join(_GRAPHIQL_STATIC_DIR, name))
        for name in _GRAPHIQL_ASSETS
    )


def graphql_install(verbose: bool = True) -> None:
    """Download GraphiQL playground assets into asok/api/static/graphiql/."""
    os.makedirs(_GRAPHIQL_STATIC_DIR, exist_ok=True)
    total = len(_GRAPHIQL_ASSETS)
    Style.heading("INSTALLING GRAPHIQL ASSETS")
    for i, (filename, url) in enumerate(_GRAPHIQL_ASSETS.items(), 1):
        dest = os.path.join(_GRAPHIQL_STATIC_DIR, filename)
        if os.path.isfile(dest):
            _log_info(f"  [{i}/{total}] {filename} already present, skipping", verbose)
            continue
        _log_info(f"  [{i}/{total}] Downloading {filename}...", verbose)
        if not url.startswith("https://"):
            raise RuntimeError(f"Security error: only HTTPS downloads allowed ({url})")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            raise RuntimeError(f"Failed to download {filename}: {e}")
    Style.success("GraphiQL assets installed → asok/api/static/graphiql/")


def _start_tailwind_watcher(root: str) -> subprocess.Popen | None:
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
