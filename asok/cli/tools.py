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


def tailwind_install(root: str, verbose: bool = True) -> str:
    """Download the pinned Tailwind binary into .asok/bin/ if missing or outdated."""
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

    # SECURITY: Verify URL is HTTPS
    if not url.startswith("https://"):
        raise RuntimeError("Security error: only HTTPS downloads are allowed")

    if verbose:
        print(f"  Downloading Tailwind v{TAILWIND_VERSION} ({suffix})...")

    # SECURITY NOTE: Binary is downloaded over HTTPS from the official GitHub repository.
    # For maximum security in production environments, consider verifying the SHA256 checksum
    # from the official release page: https://github.com/tailwindlabs/tailwindcss/releases
    #
    # Example verification code (disabled by default):
    # import hashlib
    # with open(bin_path, 'rb') as f:
    #     actual_hash = hashlib.sha256(f.read()).hexdigest()
    # expected_hash = "..."  # Get from official release page
    # if actual_hash != expected_hash:
    #     os.remove(bin_path)
    #     raise RuntimeError("Checksum verification failed")

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


def tailwind_build(root: str, minify: bool = False) -> None:
    """Run a one-shot Tailwind build.

    SECURITY: All paths are validated to prevent command injection.
    """
    bin_path = _tailwind_binary_path(root)
    if not os.path.isfile(bin_path):
        raise RuntimeError("Tailwind not installed. Run: asok tailwind --install")

    # SECURITY: Verify binary path is within expected directory
    expected_dir = os.path.join(root, ".asok", "bin")
    if not os.path.abspath(bin_path).startswith(os.path.abspath(expected_dir)):
        raise RuntimeError("Security error: invalid binary path")

    input_path = os.path.join(root, "src/partials/css/base.css")
    output_path = os.path.join(root, "src/partials/css/base.build.css")

    # SECURITY: Verify paths are within project root
    if not os.path.abspath(input_path).startswith(os.path.abspath(root)):
        raise RuntimeError("Security error: invalid input path")
    if not os.path.abspath(output_path).startswith(os.path.abspath(root)):
        raise RuntimeError("Security error: invalid output path")

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

    # 1. Ensure src/partials/css/base.css has @import "tailwindcss"
    css_path = os.path.join(root, "src/partials/css/base.css")
    os.makedirs(os.path.dirname(css_path), exist_ok=True)

    with open(css_path, "w", encoding="utf-8") as f:
        f.write('@import "tailwindcss";\n')
    Style.success("Reset src/partials/css/base.css with Tailwind import")

    # 2. Update src/partials/html/base.html (or base.asok) to use base.build.css
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

    # 3. Install and build
    tailwind_install(root, verbose=True)
    tailwind_build(root, minify=False)
    Style.success("Tailwind CSS is now enabled and ready!")


def admin_enable(root: str) -> None:
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
    totp_secret = Field.String(nullable=True, hidden=True)
    totp_enabled = Field.Boolean(default=False)
    backup_codes = Field.String(nullable=True, hidden=True)
    created_at = Field.CreatedAt()
""")
        Style.success("Created default User model in src/models/user.py")

    # 3. Check for src/models/role.py
    role_model = os.path.join(root, "src/models/role.py")
    if not os.path.isfile(role_model):
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

    # 4. Check for src/models/admin_log.py
    log_model = os.path.join(root, "src/models/admin_log.py")
    if not os.path.isfile(log_model):
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

    Style.info("Next steps:")
    print("  1. Run 'asok make migration add_admin' to detect new tables")
    print("  2. Run 'asok migrate' to apply them")
    print("  3. Run 'asok createsuperuser' to create your first account")
    print("  4. Visit /admin in your browser\n")


def _image_binary_path(root: str) -> str:
    suffix = _tailwind_platform_suffix()
    name = "cwebp.exe" if suffix.endswith(".exe") else "cwebp"
    return os.path.join(root, ".asok", "bin", name)


def image_install(root: str, verbose: bool = True) -> str:
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

    # SECURITY NOTE: Binary is downloaded over HTTPS from the official Google repository.
    # For maximum security in production environments, consider verifying the SHA256 checksum
    # from the official release page: https://developers.google.com/speed/webp/download
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


def image_optimize_all(root: str, delete_originals: bool = False) -> None:
    """Scan and optimize all existing images in the project."""
    from ..utils.image import is_image, optimize_image

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


def _esbuild_binary_path(root: str) -> str:
    suffix = _tailwind_platform_suffix()
    name = "esbuild.exe" if suffix.endswith(".exe") else "esbuild"
    return os.path.join(root, ".asok", "bin", name)


def assets_install(root: str, verbose: bool = True) -> str:
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

    # SECURITY NOTE: Binary is downloaded over HTTPS from the official npm registry.
    # For maximum security in production environments, consider verifying the SHA256 checksum
    # from the official npm package: https://www.npmjs.com/package/@esbuild/{npm_pkg}
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


def assets_minify(root: str) -> None:
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
