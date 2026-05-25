from __future__ import annotations

import importlib.util as _ilu
import os
import signal
import socket
import sys
import time
import traceback
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .style import Style


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


def _project_uses_tailwind(root: str) -> bool:
    css_path = os.path.join(root, "src/partials/css/base.css")
    if not os.path.isfile(css_path):
        return False
    try:
        with open(css_path, "r", encoding="utf-8") as f:
            return '@import "tailwindcss"' in f.read()
    except OSError:
        return False


def _find_project_root(start=None) -> str | None:
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


def get_last_mtime() -> float:
    """Get the maximum modification time among all watched files in the project."""
    max_mtime = 0.0
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
    watch_exts = (".py", ".html", ".asok", ".json", ".css", ".js")

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


def _has_py_changed(since_mtime: float) -> bool:
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
            if f.endswith((".py", ".json")) or f in (".env", "wsgi.py"):
                try:
                    if os.stat(os.path.join(root, f)).st_mtime > since_mtime:
                        return True
                except OSError:
                    pass
    return False


def _find_free_port(start: int = 8000, end: int = 8100) -> int | None:
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


def _start_server(port: int) -> int | None:
    """Fork a child process that runs the WSGI server on the given port."""
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


def _kill_child(pid: int) -> None:
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


def run_dev(port_arg: int | None = None) -> None:
    """Start the Asok development server with auto-reloading and Tailwind CSS integration."""
    from .tools import _start_tailwind_watcher

    os.environ.pop("ASOK_CLI", None)
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
        if pid is not None:
            _kill_child(pid)
        if tw_proc:
            tw_proc.terminate()
        sys.exit(0)


def run_preview(port_arg: int | None = None) -> None:
    """Run the app in production mode locally (no reload, no debug)."""
    from .tools import _esbuild_binary_path, assets_minify, tailwind_build

    os.environ.pop("ASOK_CLI", None)
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
