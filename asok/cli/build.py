from __future__ import annotations

import os
import py_compile
import shutil
import subprocess

from ..utils.minify import minify_html
from .style import Style


def _minify_html(html: str) -> str:
    """Minifies HTML content using the central safe minifier."""
    return minify_html(html)


def run_build(
    root: str,
    keep_source: bool = False,
    with_db: bool = False,
    output: str | None = None,
) -> None:
    """Generate a production-ready optimized distribution."""
    from .server import _project_uses_tailwind
    from .tools import _esbuild_binary_path, _tailwind_binary_path

    Style.heading("BUILDING PRODUCTION DISTRIBUTION")

    app_name = output or "dist"
    build_root = os.path.join(root, app_name)

    if os.path.exists(build_root):
        shutil.rmtree(build_root)
    os.makedirs(build_root)

    Style.info(f"Cloning project to {Style.BOLD}{app_name}/{Style.RESET}...")

    # Exclude development-only files and folders
    ignore_list = [
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
        "tests",
    ]
    if not with_db:
        ignore_list.append("db.sqlite3*")

    # Also ignore the output directory itself if it's inside the root
    if app_name not in ignore_list:
        ignore_list.append(app_name)

    ignore = shutil.ignore_patterns(*ignore_list)

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

    # 1b. Cleanup .gitignore in distribution (it often ignores built assets like base.build.css)
    gitignore_path = os.path.join(build_root, ".gitignore")
    if os.path.exists(gitignore_path):
        try:
            os.remove(gitignore_path)
            Style.success("Cleaned up development .gitignore from distribution.")
        except OSError:
            pass

    # 2. Assets Minification (Universal & Scoped Assets)
    bin_path = _esbuild_binary_path(root)
    if os.path.isfile(bin_path):
        Style.info("Minifying JS and CSS assets (including scoped assets)...")
        # Target everything in src/ to cover pages, components, and partials
        target_dir = os.path.join(build_root, "src")
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
            if f.endswith(".html") or f.endswith(".asok"):
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
        # SKIP migrations directory for compilation as it needs to remain editable/readable in prod
        if "src/migrations" in r.replace("\\", "/"):
            continue

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
            # Do NOT remove migrations even if keep_source is False
            if "src/migrations" in r.replace("\\", "/"):
                continue

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
            from ..utils.image import is_image, optimize_image

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
        f.write("ASOK_BUILD=true\n")
        f.write("SECRET_KEY=change-me-for-production\n")
        f.write("ALLOWED_HOSTS=*\n")
        f.write("IMAGE_OPTIMIZATION=true\n")

    Style.success(
        f"Build complete! Distribution ready in: {Style.BOLD}{app_name}/{Style.RESET}"
    )
    print(f"  {Style.DIM}To preview: cd {app_name} && asok preview{Style.RESET}\n")
