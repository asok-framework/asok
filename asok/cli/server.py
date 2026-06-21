from __future__ import annotations

import importlib.util as _ilu
import os
import signal
import socket
import sys
import time
import traceback
from typing import Any
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


def _has_wsgi(directory: str) -> bool:
    return os.path.isfile(os.path.join(directory, "wsgi.py")) or os.path.isfile(
        os.path.join(directory, "wsgi.pyc")
    )


def _find_project_root(start=None) -> str | None:
    cur = start or os.getcwd()
    for _ in range(10):
        if _has_wsgi(cur):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
    return None


_IGNORE_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", "uploads", ".asok", "deployment"}
_WATCH_EXTS = (".py", ".html", ".asok", ".json", ".css", ".js")


def _collect_watch_dirs() -> list[str]:
    """Collect directories to watch: current dir plus any local editable packages."""
    import importlib
    watch_dirs = ["."]
    for pkg in ("asok", "asok_lucide"):
        try:
            mod = importlib.import_module(pkg)
            p = getattr(mod, "__file__", None)
            if p and "site-packages" not in p:
                watch_dirs.append(os.path.dirname(p))
        except Exception:
            pass
    return watch_dirs


def _is_ignored_file(f: str) -> bool:
    if f == "base.build.css":
        return True
    if f.startswith(".") and f != ".env":
        return True
    return False


def _update_max_mtime(root: str, f: str, max_mtime: float) -> float:
    try:
        mtime = os.stat(os.path.join(root, f)).st_mtime
        if mtime > max_mtime:
            return mtime
    except OSError:
        pass
    return max_mtime


def _process_dir_files(root: str, files: list[str], max_mtime: float) -> float:
    for f in files:
        if _is_ignored_file(f):
            continue
        if f.endswith(_WATCH_EXTS) or f == ".env":
            max_mtime = _update_max_mtime(root, f, max_mtime)
    return max_mtime


def _filter_dirs(dirs: list[str]) -> list[str]:
    return [d for d in dirs if d not in _IGNORE_DIRS and not d.startswith(".")]


def get_last_mtime() -> float:
    """Get the maximum modification time among all watched files in the project."""
    max_mtime = 0.0
    for base in _collect_watch_dirs():
        for root, dirs, files in os.walk(base):
            dirs[:] = _filter_dirs(dirs)
            max_mtime = _process_dir_files(root, files, max_mtime)
    return max_mtime


def _check_file_mtime(root: str, f: str, since_mtime: float) -> bool:
    try:
        if os.stat(os.path.join(root, f)).st_mtime > since_mtime:
            return True
    except OSError:
        pass
    return False


def _any_file_changed(root: str, files: list[str], since_mtime: float) -> bool:
    for f in files:
        if f.endswith((".py", ".json")) or f in (".env", "wsgi.py"):
            if _check_file_mtime(root, f, since_mtime):
                return True
    return False


def _has_py_changed(since_mtime: float) -> bool:
    """Check if any .py or .env file was modified after since_mtime."""
    for base in _collect_watch_dirs():
        for root, dirs, files in os.walk(base):
            dirs[:] = _filter_dirs(dirs)
            if _any_file_changed(root, files, since_mtime):
                return True
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


def _wait_for_child_exit(pid: int) -> bool:
    for _ in range(20):
        try:
            result = os.waitpid(pid, os.WNOHANG)
            if result[0] != 0:
                return True
        except ChildProcessError:
            return True
        time.sleep(0.05)
    return False


def _force_kill_child(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


def _kill_child(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    if _wait_for_child_exit(pid):
        return

    _force_kill_child(pid)


def _load_wsgi_app(wsgi_path: str) -> Any:
    """Import and return the WSGI app from a wsgi.py path. Returns None on failure."""
    try:
        spec = _ilu.spec_from_file_location("wsgi", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.app
    except Exception as e:
        print(f"Error loading WSGI entry point: {e}")
        traceback.print_exc()
        return None


def _find_wsgi_path(root: str = None) -> str | None:
    """Find wsgi.py or wsgi.pyc in the given root (defaults to cwd)."""
    root = root or os.getcwd()
    for name in ("wsgi.py", "wsgi.pyc"):
        p = os.path.join(root, name)
        if os.path.isfile(p):
            return p
    return None


def _configure_debug_logging() -> None:
    import logging
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        f"{Style.DIM}%(levelname)s:{Style.RESET}{Style.DIM}%(name)s:{Style.RESET} %(message)s"
    )
    console.setFormatter(formatter)
    logging.getLogger("asok.security").addHandler(console)
    logging.getLogger("asok.security").setLevel(logging.DEBUG)


def _load_wsgi_and_setup_logging(wsgi_path: str) -> Any:
    try:
        spec = _ilu.spec_from_file_location("wsgi", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        app = mod.app
        if app.config.get("DEBUG"):
            _configure_debug_logging()
        return app
    except Exception as e:
        print(f"Error loading WSGI entry point: {e}")
        traceback.print_exc()
        sys.exit(1)


def _run_server_child(wsgi_path: str, port: int) -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    app = _load_wsgi_and_setup_logging(wsgi_path)
    print(f"Starting Asok development server on http://127.0.0.1:{port}")
    WSGIServer.allow_reuse_address = True
    httpd = make_server("127.0.0.1", port, app, handler_class=_QuietHandler)

    def _shutdown(sig, frame):
        httpd.server_close()
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    httpd.serve_forever()


def _start_server(port: int) -> int | None:
    """Fork a child process that runs the WSGI server on the given port."""
    wsgi_path = _find_wsgi_path()
    if not wsgi_path:
        print(f"Error: WSGI entry point (wsgi.py/c) not found in {os.getcwd()}")
        return None

    pid = os.fork() if hasattr(os, "fork") else 0
    if pid == 0:  # Child (Server)
        _run_server_child(wsgi_path, port)

    return pid


def _handle_change(port: int, pid: int, tw_proc: Any, last_mtime: float, current_mtime: float) -> tuple[int | None, float, bool]:
    py_changed = _has_py_changed(last_mtime)
    if py_changed:
        print(f"  {Style.YELLOW}↻{Style.RESET} {Style.DIM}Python change, restarting...{Style.RESET}")
        _kill_child(pid)
        new_pid = _start_server(port)
        if new_pid is None:
            if tw_proc:
                tw_proc.terminate()
            return None, current_mtime, True
        return new_pid, current_mtime, False
    else:
        print(f"  {Style.CYAN}⚡{Style.RESET} {Style.DIM}Asset change, reloading...{Style.RESET}")
        return pid, current_mtime, False


def _shutdown_dev_server(pid: int, tw_proc: Any) -> None:
    if pid is not None:
        _kill_child(pid)
    if tw_proc:
        tw_proc.terminate()
    sys.exit(0)


def _run_dev_loop(port: int, pid: int, tw_proc: Any) -> None:
    """Main watch loop for the dev server: detect changes and restart as needed."""
    last_mtime = get_last_mtime()
    try:
        while True:
            time.sleep(1)
            current_mtime = get_last_mtime()
            if current_mtime > last_mtime:
                pid, last_mtime, should_stop = _handle_change(port, pid, tw_proc, last_mtime, current_mtime)
                if should_stop:
                    return
    except KeyboardInterrupt:
        _shutdown_dev_server(pid, tw_proc)


def _get_dev_port(port_arg: int | None) -> int | None:
    requested_port = port_arg or int(os.environ.get("ASOK_PORT", "8000"))
    port = _find_free_port(requested_port)
    if port is None:
        print(f"Error: No free port found between {requested_port} and {requested_port + 100}")
        return None
    if port != requested_port:
        Style.warn(f"Port {requested_port} is in use, using {port} instead")
        print()
    return port


def _print_dev_banner(port: int, tw_proc: Any) -> None:
    Style.heading("DEVELOPMENT SERVER")
    print(f"  {Style.DIM}Reloader {Style.RESET}{Style.GREEN}●{Style.RESET}{Style.DIM} Active (PID: {os.getpid()}){Style.RESET}")
    print(f"  {Style.DIM}URL      {Style.RESET}{Style.BOLD}http://127.0.0.1:{port}{Style.RESET}")
    if tw_proc:
        print(f"  {Style.DIM}Tailwind {Style.RESET}{Style.GREEN}●{Style.RESET}{Style.DIM} Watching...{Style.RESET}")
    print()


def run_dev(port_arg: int | None = None) -> None:
    """Start the Asok development server with auto-reloading and Tailwind CSS integration."""
    from .tools import _start_tailwind_watcher

    os.environ.pop("ASOK_CLI", None)
    sys.path.insert(0, os.getcwd())

    port = _get_dev_port(port_arg)
    if port is None:
        return

    pid = _start_server(port)
    if pid is None:
        return

    tw_proc = None
    if _project_uses_tailwind(os.getcwd()):
        tw_proc = _start_tailwind_watcher(os.getcwd())

    _print_dev_banner(port, tw_proc)
    _run_dev_loop(port, pid, tw_proc)


def _load_env_file(env_path: str) -> None:
    """Load key=value pairs from a .env file into os.environ."""
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()


def _prepare_preview_env() -> None:
    env_path = os.path.join(os.getcwd(), ".env")
    if os.path.exists(env_path):
        _load_env_file(env_path)
    os.environ["DEBUG"] = "false"


def _build_preview_assets() -> None:
    from .tools import _esbuild_binary_path, assets_minify, tailwind_build
    root = os.getcwd()
    es_bin = _esbuild_binary_path(root)
    if os.path.exists(es_bin):
        assets_minify(root)

    if _project_uses_tailwind(root):
        try:
            tailwind_build(root, minify=True)
        except RuntimeError as e:
            Style.error(f"Tailwind build failed: {e}")


def _get_preview_port(port_arg: int | None) -> int | None:
    requested_port = port_arg or int(os.environ.get("ASOK_PORT", "8000"))
    port = _find_free_port(requested_port)
    if port is None:
        print("Error: No free port found")
        return None
    if port != requested_port:
        print(f"  Port {requested_port} is in use, using {port} instead")
    return port


def _run_preview_server(port: int, app: Any) -> None:
    Style.heading("PREVIEW SERVER (PRODUCTION MODE)")
    print(f"  {Style.DIM}URL  {Style.RESET}{Style.BOLD}http://127.0.0.1:{port}{Style.RESET}")
    Style.info("No auto-reload — restart manually after changes\n")

    WSGIServer.allow_reuse_address = True
    httpd = make_server("127.0.0.1", port, app, handler_class=_QuietHandler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


def run_preview(port_arg: int | None = None) -> None:
    """Run the app in production mode locally (no reload, no debug)."""
    os.environ.pop("ASOK_CLI", None)
    sys.path.insert(0, os.getcwd())

    _prepare_preview_env()
    _build_preview_assets()

    root = os.getcwd()
    wsgi_path = _find_wsgi_path(root)
    if not wsgi_path:
        print(f"Error: WSGI entry point (wsgi.py/c) not found in {root}")
        return

    port = _get_preview_port(port_arg)
    if port is None:
        return

    app = _load_wsgi_app(wsgi_path)
    if not app:
        return

    _run_preview_server(port, app)
