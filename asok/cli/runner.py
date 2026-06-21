from __future__ import annotations

import code
import importlib.util as _ilu
import os
import sys
import unittest

from ..orm import MODELS_REGISTRY, Model
from .style import Style


def _check_route_file(root: str, files: list[str], pages_dir: str) -> tuple[str, str] | None:
    if "page.py" not in files and "page.html" not in files:
        return None
    rel = os.path.relpath(root, pages_dir).replace(os.sep, "/")
    url = "/" if rel == "." else "/" + rel
    handler = "page.py" if "page.py" in files else "page.html"
    return url, handler


def _find_routes(pages_dir: str) -> list[tuple[str, str]]:
    routes = []
    for root, _, files in os.walk(pages_dir):
        route = _check_route_file(root, files, pages_dir)
        if route:
            routes.append(route)
    return routes


def _print_routes_list(routes: list[tuple[str, str]]) -> None:
    u_width = max(len(u) for u, _ in routes)
    print(f"  {Style.BOLD}{Style.DIM}{'URL'.ljust(u_width)}   {'HANDLER'}{Style.RESET}")
    print(f"  {Style.DIM}{'-' * u_width}   {'-' * 15}{Style.RESET}")
    for url, handler in routes:
        h_color = Style.GREEN if handler.endswith(".py") else Style.CYAN
        print(
            f"  {Style.BOLD}{url.ljust(u_width)}{Style.RESET}   {h_color}{handler}{Style.RESET}"
        )
    print()


def run_routes() -> None:
    """List all routes by walking src/pages/."""
    Style.heading("ROUTES")
    pages_dir = os.path.join(os.getcwd(), "src/pages")
    if not os.path.isdir(pages_dir):
        Style.error("No src/pages/ directory found.")
        return
    routes = _find_routes(pages_dir)
    routes.sort()
    if not routes:
        Style.info("No routes found.")
        return

    _print_routes_list(routes)


def _parse_env_line(line: str) -> None:
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def _load_env() -> None:
    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            _parse_env_line(line.strip())


def _load_app_instance(ns: dict) -> None:
    wsgi_path = os.path.join(os.getcwd(), "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(os.getcwd(), "wsgi.pyc")

    if not os.path.isfile(wsgi_path):
        return

    try:
        spec = _ilu.spec_from_file_location("_wsgi", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "app"):
            ns["app"] = mod.app
    except Exception as e:
        Style.warn(f"Could not load 'app' from WSGI entry point: {e}")


def _load_single_model(model_dir: str, filename: str) -> None:
    filepath = os.path.join(model_dir, filename)
    spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)


def _load_all_models(ns: dict) -> None:
    model_dir = os.path.join(os.getcwd(), "src/models")
    if not os.path.isdir(model_dir):
        return
    for filename in sorted(os.listdir(model_dir)):
        if filename.endswith(".py") and not filename.startswith("__"):
            _load_single_model(model_dir, filename)


def _try_import_readline() -> None:
    try:
        import readline  # noqa: F401
    except ImportError:
        pass


def run_shell() -> None:
    """Interactive Python shell with all models pre-imported."""
    banner = f"{Style.BOLD}{Style.CYAN}Asok Shell{Style.RESET} {Style.DIM}(Interactive Python){Style.RESET}"
    print(f"\n{banner}")
    Style.info("All models and 'app' instance pre-imported.\n")
    sys.path.insert(0, os.getcwd())

    _load_env()

    ns = {"Model": Model}
    _load_app_instance(ns)
    _load_all_models(ns)

    ns.update(MODELS_REGISTRY)
    interact_banner = (
        f"Asok shell — models loaded: {', '.join(MODELS_REGISTRY) or '(none)'}\n"
        f"Python {sys.version.split()[0]}"
    )
    _try_import_readline()
    code.interact(banner=interact_banner, local=ns)


def run_test(path: str | None = None) -> None:
    """Discover and run tests in tests/ directory."""
    sys.path.insert(0, os.getcwd())

    target = path or "tests"
    if not os.path.isdir(target):
        print(f"No '{target}/' directory found.")
        return
    loader = unittest.TestLoader()
    suite = loader.discover(target)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
