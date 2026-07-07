from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import subprocess

from ..utils.minify import minify_html
from .style import Style


def _minify_html(html: str) -> str:
    """Minifies HTML content using the central safe minifier."""
    return minify_html(html)


_DEFAULT_IGNORE = (
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
)


def run_build(
    root: str,
    keep_source: bool = False,
    with_db: bool = False,
    output: str | None = None,
) -> None:
    """Generate a production-ready optimized distribution."""
    Style.heading("BUILDING PRODUCTION DISTRIBUTION")
    app_name = output or "dist"
    build_root = os.path.join(root, app_name)
    _clone_project_tree(root, build_root, app_name, with_db)
    _precompile_directives_stage(build_root)
    _tailwind_stage(root, build_root)
    _cleanup_gitignore(build_root)
    _minify_assets_stage(root, build_root)
    _minify_html_stage(build_root)
    _python_compile_stage(build_root, keep_source)
    _image_optimization_stage(build_root)
    _write_production_env(build_root)
    _ssg_stage(build_root)
    _print_build_complete(app_name)


# ── 0. Clone project ────────────────────────────────────────────────


def _clone_project_tree(
    root: str, build_root: str, app_name: str, with_db: bool
) -> None:
    if os.path.exists(build_root):
        shutil.rmtree(build_root)
    os.makedirs(build_root)
    Style.info(f"Cloning project to {Style.BOLD}{app_name}/{Style.RESET}...")
    ignore_list = list(_DEFAULT_IGNORE)
    if not with_db:
        ignore_list.append("db.sqlite3*")
    if app_name not in ignore_list:
        ignore_list.append(app_name)
    shutil.copytree(
        root,
        build_root,
        ignore=shutil.ignore_patterns(*ignore_list),
        dirs_exist_ok=True,
    )


# ── 1. Directives precompilation ───────────────────────────────────


def _precompile_directives_stage(build_root: str) -> None:
    Style.info("Precompiling Asok directives...")
    try:
        app = _build_temporary_app(build_root)
    except Exception as e:
        Style.warn(f"Directives precompilation failed: {e}")
        return
    src_dir = os.path.join(build_root, "src")
    if not os.path.exists(src_dir):
        return
    global_registry = _precompile_directives_in_tree(app, src_dir)
    _write_directives_registry(app, build_root, global_registry)


def _build_temporary_app(build_root: str):
    from ..core.asok import Asok

    if "SECRET_KEY" not in os.environ:
        os.environ["SECRET_KEY"] = "static-build-key-temporary"
    return Asok(root_dir=build_root)


def _precompile_directives_in_tree(app, src_dir: str) -> dict[str, str]:
    global_registry: dict[str, str] = {}
    for r, _, files in os.walk(src_dir):
        if _is_static_assets_dir(r):
            continue
        _precompile_directives_in_dir(app, r, files, global_registry)
    return global_registry


def _precompile_directives_in_dir(app, r: str, files, global_registry: dict) -> None:
    for f in files:
        if f.endswith(".html") or f.endswith(".asok"):
            _precompile_directives_in_file(app, os.path.join(r, f), global_registry, f)


def _is_static_assets_dir(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return "src/partials/js" in normalized or "src/partials/css" in normalized


def _precompile_directives_in_file(
    app, path: str, global_registry: dict, filename: str
) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f_in:
            content = f_in.read()
        transformed_html, file_registry = app._precompile_directives(content)
        global_registry.update(file_registry)
        with open(path, "w", encoding="utf-8") as f_out:
            f_out.write(transformed_html)
    except Exception as e:
        Style.warn(f"Directives precompile failed for {filename}: {e}")


def _write_directives_registry(app, build_root: str, registry: dict) -> None:
    if not registry:
        Style.info("No Asok directives found to precompile.")
        return
    registry_js = _build_registry_js(app, registry)
    js_dir = os.path.join(build_root, "src/partials/js")
    os.makedirs(js_dir, exist_ok=True)
    with open(
        os.path.join(js_dir, "directives_registry.js"), "w", encoding="utf-8"
    ) as f:
        f.write(registry_js)
    Style.success(f"Generated static directives registry with {len(registry)} entries.")


def _build_registry_js(app, registry: dict) -> str:
    entries = [_format_registry_entry(app, h, expr) for h, expr in registry.items()]
    return (
        "window.__asok_registry = Object.assign(window.__asok_registry || {}, {\n"
        + ",\n".join(entries)
        + "\n});\n"
    )


def _format_registry_entry(app, h: str, expr: str) -> str:
    is_stmt = _expression_is_statement(expr)
    if expr.strip().startswith("{") and not is_stmt:
        expr = f"({expr})"
    body = f"return ({expr})" if not is_stmt else expr
    body = re.sub(r"\s+", " ", body).strip()
    is_async = app._is_async_expression_cached(expr)
    fn_prefix = "async " if is_async else ""
    return (
        f"    {json.dumps(h)}: {fn_prefix}function($, $store, $el, $event, $refs, $nextTick)"
        f" {{ with($||{{}}) {{ {body} }} }}"
    )


def _expression_is_statement(expr: str) -> bool:
    if ";" in expr or "return " in expr:
        return True
    return bool(re.search(r"\b(if|for|while|const|let|var|function)\b", expr))


# ── 2. Tailwind ────────────────────────────────────────────────────


def _tailwind_stage(root: str, build_root: str) -> None:
    from .server import _project_uses_tailwind
    from .tools import _tailwind_binary_path

    if not _project_uses_tailwind(root):
        return
    Style.info("Optimizing Tailwind CSS...")
    bin_path = _tailwind_binary_path(root)
    if not os.path.isfile(bin_path):
        return
    _run_tailwind_compile(root, build_root, bin_path)


def _run_tailwind_compile(root: str, build_root: str, bin_path: str) -> None:
    input_path = os.path.join(build_root, "src/partials/css/base.css")
    output_path = os.path.join(build_root, "src/partials/css/base.build.css")
    res = subprocess.run(
        [bin_path, "-i", input_path, "-o", output_path, "--minify"],
        cwd=root,
        capture_output=True,
    )
    if res.returncode != 0:
        Style.error(f"Tailwind build failed: {res.stderr.decode()}")
        return
    if os.path.exists(input_path):
        os.remove(input_path)
    Style.success("Tailwind CSS optimized and source removed.")


def _cleanup_gitignore(build_root: str) -> None:
    gitignore_path = os.path.join(build_root, ".gitignore")
    if not os.path.exists(gitignore_path):
        return
    try:
        os.remove(gitignore_path)
        Style.success("Cleaned up development .gitignore from distribution.")
    except OSError:
        pass


# ── 3. JS/CSS asset minification ───────────────────────────────────


def _minify_assets_stage(root: str, build_root: str) -> None:
    from .tools import _esbuild_binary_path

    bin_path = _esbuild_binary_path(root)
    if not os.path.isfile(bin_path):
        Style.warn(
            "Asset minification skipped (esbuild not found). Run 'asok assets --install'."
        )
        return
    Style.info("Minifying JS and CSS assets (including scoped assets)...")
    target_dir = os.path.join(build_root, "src")
    if os.path.exists(target_dir):
        _minify_assets_in_tree(root, build_root, target_dir, bin_path)
    Style.success("Universal JS/CSS assets optimized.")


def _minify_assets_in_tree(
    root: str, build_root: str, target_dir: str, bin_path: str
) -> None:
    for r, _, files in os.walk(target_dir):
        for f in files:
            if _is_minify_candidate(f):
                _minify_one_asset(root, build_root, r, f, bin_path)


def _is_minify_candidate(filename: str) -> bool:
    if not (filename.endswith(".js") or filename.endswith(".css")):
        return False
    return not filename.endswith(".build.css")


def _minify_one_asset(
    root: str, build_root: str, dir_path: str, filename: str, bin_path: str
) -> None:
    path = os.path.join(dir_path, filename)
    rel_path = os.path.relpath(path, build_root)
    print(f"  {Style.DIM}Optimizing {rel_path}...{Style.RESET}")
    res = subprocess.run(
        [bin_path, path, "--minify", f"--outfile={path}", "--allow-overwrite"],
        cwd=root,
        capture_output=True,
    )
    if res.returncode != 0:
        Style.warn(f"Minify failed for {filename}: {res.stderr.decode()}")


# ── 4. HTML minification ───────────────────────────────────────────


def _minify_html_stage(build_root: str) -> None:
    Style.info("Minifying HTML templates...")
    for r, _, files in os.walk(build_root):
        for f in files:
            if f.endswith(".html") or f.endswith(".asok"):
                _minify_one_html(os.path.join(r, f), f)
    Style.success("All HTML templates minified.")


def _minify_one_html(path: str, filename: str) -> None:
    try:
        with open(path, "r", encoding="utf-8") as f_in:
            content = f_in.read()
        minified = _minify_html(content)
        with open(path, "w", encoding="utf-8") as f_out:
            f_out.write(minified)
    except Exception as e:
        Style.warn(f"HTML minify failed for {filename}: {e}")


# ── 5. Python compilation ──────────────────────────────────────────


def _python_compile_stage(build_root: str, keep_source: bool) -> None:
    Style.info("Compiling Python source code...")
    success_count = _compile_python_tree(build_root, keep_source)
    Style.success(
        f"Compiled {success_count} Python files "
        + ("" if keep_source else "(sources removed recursively)")
    )
    if not keep_source:
        _strip_remaining_py_files(build_root)


def _compile_python_tree(build_root: str, keep_source: bool) -> int:
    count = 0
    for r, _, files in os.walk(build_root):
        if _is_migrations_dir(r):
            continue
        count += _compile_py_files_in_dir(r, files, keep_source)
    return count


def _is_migrations_dir(path: str) -> bool:
    # Migrations must remain readable in prod for tooling.
    return "src/migrations" in path.replace("\\", "/")


def _compile_py_files_in_dir(r: str, files, keep_source: bool) -> int:
    return sum(
        1 for f in files if f.endswith(".py") and _compile_one_py(r, f, keep_source)
    )


def _compile_one_py(dir_path: str, filename: str, keep_source: bool) -> bool:
    src = os.path.join(dir_path, filename)
    dst = src + "c"
    if not _try_py_compile(src, dst, filename):
        return False
    if not (os.path.exists(dst) and os.path.getsize(dst) > 0):
        return False
    if not keep_source:
        _safe_remove(src)
    return True


def _try_py_compile(src: str, dst: str, filename: str) -> bool:
    try:
        py_compile.compile(src, cfile=dst, optimize=1)
        return True
    except Exception as e:
        Style.warn(f"Compile failed for {filename}: {e}")
        return False


def _strip_remaining_py_files(build_root: str) -> None:
    for r, _, files in os.walk(build_root):
        if _is_migrations_dir(r):
            continue
        _remove_py_files(r, files)


def _remove_py_files(r: str, files) -> None:
    for f in files:
        if f.endswith(".py"):
            _safe_remove(os.path.join(r, f))


def _safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


# ── 6. Images ──────────────────────────────────────────────────────


def _image_optimization_stage(build_root: str) -> None:
    if os.environ.get("IMAGE_OPTIMIZATION") != "true":
        Style.info("Image optimization skipped (IMAGE_OPTIMIZATION not enabled).")
        return
    Style.info("Optimizing project images to WebP...")
    try:
        from ..utils.image import is_image, optimize_image
    except ImportError:
        Style.warn("Image optimization skipped (Pillow not installed).")
        return
    optimized = _optimize_images_in_tree(build_root, is_image, optimize_image)
    Style.success(f"Optimized {optimized} images to WebP (originals removed).")


def _optimize_images_in_tree(build_root: str, is_image, optimize_image) -> int:
    count = 0
    for r, _, files in os.walk(build_root):
        count += _optimize_images_in_dir(r, files, is_image, optimize_image)
    return count


def _optimize_images_in_dir(r: str, files, is_image, optimize_image) -> int:
    return sum(
        1
        for f in files
        if is_image(f)
        and not f.endswith(".webp")
        and _optimize_one_image(os.path.join(r, f), optimize_image)
    )


def _optimize_one_image(path: str, optimize_image) -> bool:
    try:
        optimize_image(path, keep_original=False)
        return True
    except Exception:
        return False


# ── 7. Production env + SSG ────────────────────────────────────────


def _write_production_env(build_root: str) -> None:
    env_prod = os.path.join(build_root, ".env.production")
    with open(env_prod, "w") as f:
        f.write("DEBUG=false\n")
        f.write("ASOK_BUILD=true\n")
        f.write("SECRET_KEY=change-me-for-production\n")
        f.write("ALLOWED_HOSTS=*\n")
        f.write("IMAGE_OPTIMIZATION=true\n")


def _ssg_stage(build_root: str) -> None:
    Style.info("Pre-rendering static pages (SSG)...")
    try:
        app = _import_built_wsgi_app(build_root)
    except Exception as e:
        Style.warn(f"Static site pre-rendering failed or skipped: {e}")
        return
    if app is None:
        Style.warn("WSGI entry point not found in distribution; skipping SSG.")
        return
    app.root_dir = os.path.abspath(build_root)
    app.pre_generate_ssg_site()
    Style.success("Static site pre-rendering complete!")


def _import_built_wsgi_app(build_root: str):
    import importlib.util as _ilu
    import sys

    os.environ["SECRET_KEY"] = "static-build-key-temporary"
    wsgi_path = _find_wsgi_entry(build_root)
    if wsgi_path is None:
        return None
    sys.path.insert(0, build_root)
    try:
        spec = _ilu.spec_from_file_location("wsgi_prod", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.app
    finally:
        if sys.path and sys.path[0] == build_root:
            sys.path.pop(0)


def _find_wsgi_entry(build_root: str):
    for candidate in ("wsgi.py", "wsgi.pyc"):
        path = os.path.join(build_root, candidate)
        if os.path.isfile(path):
            return path
    return None


def _print_build_complete(app_name: str) -> None:
    Style.success(
        f"Build complete! Distribution ready in: {Style.BOLD}{app_name}/{Style.RESET}"
    )
    print(f"  {Style.DIM}To preview: cd {app_name} && asok preview{Style.RESET}\n")
