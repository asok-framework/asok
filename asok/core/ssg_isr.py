from __future__ import annotations

import inspect
import io
import logging
import os
import re
import sys
import time
from typing import Any, Callable, Optional

from ..background import background
from ..request import Request

logger = logging.getLogger("asok.ssg_isr")


class SSGISRMixin:
    """Mixin class for Asok that handles SSG (Static Site Generation) and ISR (Incremental Static Regeneration)."""

    def _get_ssg_cache_dir(self) -> str:
        """Returns the directory where SSG/ISR cached files are stored."""
        return os.path.join(self.root_dir, ".asok", "ssg_cache")

    def _get_ssg_cache_file(self, path: str) -> str:
        """Returns the file path for a cached request path."""
        clean_path = path.strip("/")
        if not clean_path:
            clean_path = "index"
        cache_dir = self._get_ssg_cache_dir()
        candidate = os.path.realpath(os.path.join(cache_dir, f"{clean_path}.html"))
        real_cache_dir = os.path.realpath(cache_dir)
        if not candidate.startswith(real_cache_dir + os.sep):
            raise ValueError(f"Path traversal bloqué : {path!r}")
        return candidate

    def _check_py_module_static(self, page_file: str) -> tuple[bool, Optional[int]]:
        try:
            module = self._load_module(page_file)
            revalidate = getattr(module, "REVALIDATE", None)
            is_ssg_or_isr = (
                getattr(module, "SSG", False) is True
                or hasattr(module, "get_static_paths")
                or revalidate is not None
            )
            return is_ssg_or_isr, revalidate
        except Exception as e:
            logger.error(f"Error loading page module {page_file} for SSG/ISR: {e}")
            return False, None

    def _resolve_static_page_file(
        self, request: Request
    ) -> tuple[Optional[str], dict[str, Any]]:
        parts = [p for p in request.path.split("/") if p]
        return self._resolve_route(parts)

    def _evaluate_page_file_type(self, page_file: str) -> tuple[bool, Optional[int]]:
        if page_file.endswith((".py", ".pyc")):
            return self._check_py_module_static(page_file)
        if page_file.endswith((".html", ".asok")):
            # BUG-3 fix: only treat a pure HTML/Asok template as statically
            # cacheable when there is *no* companion .py controller.  If a .py
            # controller exists, defer to its SSG/REVALIDATE flags instead —
            # otherwise dynamic pages would be incorrectly frozen as SSG.
            base = os.path.splitext(page_file)[0]
            for py_ext in (".py", ".pyc"):
                companion = base + py_ext
                if os.path.isfile(companion):
                    return self._check_py_module_static(companion)
            # No companion .py → pure static template, always SSG-eligible.
            return True, None
        return False, None

    def _check_request_is_static(
        self, request: Request
    ) -> tuple[bool, Optional[int], Optional[str], dict[str, Any]]:
        if self.config.get("DEBUG") or request.method not in ("GET", "HEAD"):
            return False, None, None, {}

        page_file, route_params = self._resolve_static_page_file(request)
        if not page_file:
            return False, None, None, {}

        is_ssg_or_isr, revalidate = self._evaluate_page_file_type(page_file)
        return is_ssg_or_isr, revalidate, page_file, route_params

    def _is_cache_stale(self, age: float, revalidate: Optional[int]) -> bool:
        return revalidate is not None and age >= revalidate

    def _trigger_background_revalidation(self, path: str, cache_file: str) -> None:
        parts = [p for p in path.split("/") if p]
        page_file, _ = self._resolve_route(parts)
        if page_file:
            background(self._regenerate_ssg_page, path, page_file, cache_file)

    def _send_cached_file(
        self,
        request: Request,
        cache_file: str,
        revalidate: Optional[int],
        start_response: Callable,
    ) -> Optional[list[bytes]]:
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                content = f.read()

            content = self._inject_assets(
                content, request, getattr(request, "nonce", "")
            )

            body = content.encode("utf-8")
            headers = [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(body))),
                ("X-Asok-SSG-Cache", "HIT"),
            ]
            if revalidate is not None:
                headers.append(("Cache-Control", f"public, max-age={revalidate}"))
            else:
                headers.append(("Cache-Control", "public, max-age=86400"))

            headers += self._cookie_headers(request, request.environ)
            headers += self._security_headers(
                request=request, nonce=getattr(request, "nonce", None)
            )

            start_response("200 OK", headers)
            return [b""] if request.method == "HEAD" else [body]
        except Exception as e:
            logger.error(f"Failed to serve cached file {cache_file}: {e}")
            return None

    def _serve_ssg_cache(
        self,
        request: Request,
        cache_file: str,
        revalidate: Optional[int],
        start_response: Callable,
    ) -> Optional[list[bytes]]:
        if not os.path.exists(cache_file):
            return None

        try:
            mtime = os.path.getmtime(cache_file)
            age = time.time() - mtime
        except OSError:
            return None

        if self._is_cache_stale(age, revalidate):
            logger.info(
                f"SSG/ISR Cache stale for path {request.path} (age: {int(age)}s). Regenerating..."
            )
            self._trigger_background_revalidation(request.path, cache_file)

        return self._send_cached_file(request, cache_file, revalidate, start_response)

    def _build_and_send_generated_response(
        self,
        request: Request,
        raw_html: str,
        revalidate: Optional[int],
        start_response: Callable,
    ) -> list[bytes]:
        content = self._inject_assets(raw_html, request, getattr(request, "nonce", ""))
        body = content.encode("utf-8")
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("X-Asok-SSG-Cache", "MISS"),
        ]
        if revalidate is not None:
            headers.append(("Cache-Control", f"public, max-age={revalidate}"))
        else:
            headers.append(("Cache-Control", "public, max-age=86400"))

        headers += self._cookie_headers(request, request.environ)
        headers += self._security_headers(
            request=request, nonce=getattr(request, "nonce", None)
        )

        start_response("200 OK", headers)
        return [b""] if request.method == "HEAD" else [body]

    def _generate_ssg_on_demand(
        self,
        request: Request,
        page_file: str,
        cache_file: str,
        revalidate: Optional[int],
        route_params: dict[str, Any],
        start_response: Callable,
    ) -> Optional[list[bytes]]:
        if revalidate is None and len(route_params) > 0:
            return None

        try:
            raw_html = self._generate_ssg_page_sync(request.path, page_file, cache_file)
            if raw_html:
                return self._build_and_send_generated_response(
                    request, raw_html, revalidate, start_response
                )
        except Exception as e:
            logger.error(
                f"Failed to generate SSG page on-demand for {request.path}: {e}"
            )
        return None

    def _handle_ssg_isr_request(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        """Checks if a request path has a pre-rendered static page and serves it if appropriate."""
        is_ssg_or_isr, revalidate, page_file, route_params = (
            self._check_request_is_static(request)
        )
        if not is_ssg_or_isr or not page_file:
            return None

        cache_file = self._get_ssg_cache_file(request.path)
        res = self._serve_ssg_cache(request, cache_file, revalidate, start_response)
        if res is not None:
            return res

        return self._generate_ssg_on_demand(
            request, page_file, cache_file, revalidate, route_params, start_response
        )

    def _render_and_cache_html(
        self, request: Request, environ: dict[str, Any], cache_file: str
    ) -> Optional[str]:
        raw_html = self._dispatch_controller(request, environ)
        if inspect.iscoroutine(raw_html):
            from ..core.asgi import async_to_sync

            raw_html = async_to_sync(raw_html)

        if raw_html and request.status.startswith("2"):
            # Cache the raw rendering (without the request's specific nonces/csrf tokens)
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                f.write(raw_html)
            return raw_html
        return None

    def _generate_ssg_page_sync(
        self, path: str, page_file: str, cache_file: str
    ) -> Optional[str]:
        """Generate and save the page synchronously (used on demand)."""
        environ = {
            "REQUEST_METHOD": "GET",
            "SCRIPT_NAME": "",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "SERVER_PROTOCOL": "HTTP/1.1",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": sys.stderr,
            "wsgi.multithread": True,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
            "asok.root": self.root_dir,
            "asok.app": self,
        }
        request = Request(environ)
        from ..context import request_context

        with request_context(request):
            parts = [p for p in path.split("/") if p]
            _, route_params = self._resolve_route(parts)
            request.params.update(route_params)
            request._current_page_file = page_file

            return self._render_and_cache_html(request, environ, cache_file)

    def _regenerate_ssg_page(self, path: str, page_file: str, cache_file: str) -> None:
        """Simulate a request and regenerate the static HTML page in the background."""
        try:
            self._generate_ssg_page_sync(path, page_file, cache_file)
        except Exception as e:
            logger.error(
                f"Failed to regenerate SSG/ISR cache for path {path}: {e}",
                exc_info=True,
            )

    def _add_sys_paths(self) -> list[str]:
        src_path = os.path.join(self.root_dir, "src")
        added_paths = []
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
            added_paths.append(src_path)
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)
            added_paths.append(self.root_dir)
        return added_paths

    def _get_route_path(self, root: str, file: str, pages_root: str) -> tuple[str, str]:
        full_path = os.path.join(root, file)
        rel_path = os.path.relpath(full_path, pages_root)
        url_path = rel_path.replace("\\", "/")
        base, _ = os.path.splitext(url_path)

        if base.endswith("/page"):
            base = base[:-5]
        elif base in ("page", "index"):
            base = ""
        elif base.endswith("/index"):
            base = base[:-6]

        route_path = "/" + base.strip("/")
        return route_path, full_path

    def _find_py_companion(self, file: str, root: str) -> Optional[str]:
        if file.endswith((".html", ".asok")):
            base_name, _ = os.path.splitext(file)
            for py_ext in (".py", ".pyc"):
                test_py = os.path.join(root, base_name + py_ext)
                if os.path.isfile(test_py):
                    return test_py
        return None

    def _should_render_static_route(self, target_py: str) -> bool:
        try:
            module = self._load_module(target_py)
            return (
                getattr(module, "SSG", False) is True
                or getattr(module, "REVALIDATE", None) is not None
            )
        except Exception:
            return False

    def _pre_render_static_route(
        self, route_path: str, full_path: str, file: str, root: str
    ) -> None:
        py_file = self._find_py_companion(file, root)
        should_render = True
        target_py = full_path if file.endswith((".py", ".pyc")) else py_file
        if target_py:
            should_render = self._should_render_static_route(target_py)

        if should_render:
            cache_file = self._get_ssg_cache_file(route_path)
            logger.info(f"  Pre-rendering static route {route_path}...")
            try:
                self._generate_ssg_page_sync(route_path, full_path, cache_file)
            except Exception as e:
                logger.error(f"  Failed to pre-render static page {route_path}: {e}")

    def _get_dynamic_static_paths(self, module: Any) -> Optional[list[dict]]:
        from ..core.asgi import async_to_sync

        if not hasattr(module, "get_static_paths"):
            return None
        get_paths_fn = getattr(module, "get_static_paths")
        paths = get_paths_fn()
        if inspect.iscoroutine(paths):
            paths = async_to_sync(paths)
        return paths

    def _render_single_dynamic_path(
        self, route_path: str, full_path: str, p: dict[str, Any]
    ) -> None:
        rendered_route = route_path
        for k, v in p.items():
            rendered_route = re.sub(rf"\[{k}(:\w+)?\]", str(v), rendered_route)

        cache_file = self._get_ssg_cache_file(rendered_route)
        logger.info(f"  Pre-rendering dynamic route {rendered_route}...")
        try:
            self._generate_ssg_page_sync(rendered_route, full_path, cache_file)
        except Exception as e:
            logger.error(f"  Failed to pre-render dynamic page {rendered_route}: {e}")

    def _pre_render_dynamic_route(self, route_path: str, full_path: str) -> None:
        if not full_path.endswith((".py", ".pyc")):
            return

        try:
            module = self._load_module(full_path)
            paths = self._get_dynamic_static_paths(module)
            if paths:
                for p in paths:
                    self._render_single_dynamic_path(route_path, full_path, p)
        except Exception as e:
            logger.error(f"  Failed to pre-render dynamic page {route_path}: {e}")

    def _pre_generate_file(self, root: str, file: str, pages_root: str) -> None:
        route_path, full_path = self._get_route_path(root, file, pages_root)
        is_dynamic = "[" in route_path and "]" in route_path

        if not is_dynamic:
            self._pre_render_static_route(route_path, full_path, file, root)
        else:
            self._pre_render_dynamic_route(route_path, full_path)

    def _walk_and_generate_ssg(self, pages_root: str) -> None:
        for root, _, files in os.walk(pages_root):
            for file in files:
                if file.endswith((".py", ".html", ".asok")) and not file.startswith(
                    "__"
                ):
                    self._pre_generate_file(root, file, pages_root)

    def pre_generate_ssg_site(self) -> None:
        """Pre-renders all static and configured dynamic pages of the app."""
        pages_root = os.path.join(self.root_dir, "src", "pages")
        if not os.path.isdir(pages_root):
            return

        added_paths = self._add_sys_paths()
        logger.info("Starting Static Site Generation (SSG)...")

        try:
            self._walk_and_generate_ssg(pages_root)
        finally:
            for p in added_paths:
                if p in sys.path:
                    sys.path.remove(p)
