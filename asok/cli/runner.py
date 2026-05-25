from __future__ import annotations

import code
import importlib.util as _ilu
import os
import sys
import unittest

from ..orm import MODELS_REGISTRY, Model
from .style import Style


def run_routes() -> None:
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


def run_shell() -> None:
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
