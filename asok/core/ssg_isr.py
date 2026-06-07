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
        return os.path.join(self._get_ssg_cache_dir(), f"{clean_path}.html")

    def _handle_ssg_isr_request(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        """Checks if a request path has a pre-rendered static page and serves it if appropriate."""
        if self.config.get("DEBUG"):
            return None

        if request.method not in ("GET", "HEAD"):
            return None

        # Resolve the route to see if it exists and determine its parameters
        parts = [p for p in request.path.split("/") if p]
        page_file, route_params = self._resolve_route(parts)
        if not page_file:
            return None

        # Check if the page is a python page module to see its config
        revalidate = None
        is_ssg_or_isr = False

        if page_file.endswith((".py", ".pyc")):
            try:
                module = self._load_module(page_file)
                revalidate = getattr(module, "REVALIDATE", None)
                is_ssg_or_isr = (
                    getattr(module, "SSG", False) is True
                    or hasattr(module, "get_static_paths")
                    or revalidate is not None
                )
            except Exception as e:
                logger.error(f"Error loading page module {page_file} for SSG/ISR: {e}")
                return None
        elif page_file.endswith((".html", ".asok")):
            # HTML templates are static by default
            is_ssg_or_isr = True

        if not is_ssg_or_isr:
            return None

        cache_file = self._get_ssg_cache_file(request.path)

        if os.path.exists(cache_file):
            # Page is cached. Check age for ISR revalidation
            try:
                mtime = os.path.getmtime(cache_file)
                age = time.time() - mtime
            except OSError:
                return None

            if revalidate is not None and age >= revalidate:
                # Cache is stale. Serve stale content but trigger background regeneration
                logger.info(
                    f"SSG/ISR Cache stale for path {request.path} (age: {int(age)}s, limit: {revalidate}s). "
                    f"Regenerating in background..."
                )
                background(
                    self._regenerate_ssg_page, request.path, page_file, cache_file
                )

            # Serve from cache
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Finalize the response by running asset injection dynamically
                # This ensures the current request's CSRF token and CSP nonces are correctly set
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

                start_response("200 OK", headers)
                return [b""] if request.method == "HEAD" else [body]
            except Exception as e:
                logger.error(f"Failed to serve cached file {cache_file}: {e}")
                return None

        # Cache file doesn't exist, generate it on demand (and cache if revalidate/SSG)
        if revalidate is not None or len(route_params) == 0:
            # Generate synchronously now, write to cache, and let it serve
            try:
                raw_html = self._generate_ssg_page_sync(
                    request.path, page_file, cache_file
                )
                if raw_html:
                    content = self._inject_assets(
                        raw_html, request, getattr(request, "nonce", "")
                    )
                    body = content.encode("utf-8")
                    headers = [
                        ("Content-Type", "text/html; charset=utf-8"),
                        ("Content-Length", str(len(body))),
                        ("X-Asok-SSG-Cache", "MISS"),
                    ]
                    if revalidate is not None:
                        headers.append(
                            ("Cache-Control", f"public, max-age={revalidate}")
                        )
                    else:
                        headers.append(("Cache-Control", "public, max-age=86400"))
                    start_response("200 OK", headers)
                    return [b""] if request.method == "HEAD" else [body]
            except Exception as e:
                logger.error(
                    f"Failed to generate SSG page on-demand for {request.path}: {e}"
                )

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
            "asok.secret_key": self.config.get("SECRET_KEY"),
        }
        request = Request(environ)
        from ..context import request_context

        with request_context(request):
            parts = [p for p in path.split("/") if p]
            _, route_params = self._resolve_route(parts)
            request.params.update(route_params)
            request._current_page_file = page_file

            # Setup page metadata / static features
            # Generates HTML content from controller
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

    def _regenerate_ssg_page(self, path: str, page_file: str, cache_file: str) -> None:
        """Simulate a request and regenerate the static HTML page in the background."""
        try:
            self._generate_ssg_page_sync(path, page_file, cache_file)
        except Exception as e:
            logger.error(
                f"Failed to regenerate SSG/ISR cache for path {path}: {e}",
                exc_info=True,
            )

    def pre_generate_ssg_site(self) -> None:
        """Pre-renders all static and configured dynamic pages of the app."""
        from ..core.asgi import async_to_sync

        pages_root = os.path.join(self.root_dir, "src", "pages")
        if not os.path.isdir(pages_root):
            return

        # Ensure build paths are in sys.path so modules can import dependencies from dist/src
        src_path = os.path.join(self.root_dir, "src")
        added_paths = []
        if src_path not in sys.path:
            sys.path.insert(0, src_path)
            added_paths.append(src_path)
        if self.root_dir not in sys.path:
            sys.path.insert(0, self.root_dir)
            added_paths.append(self.root_dir)

        logger.info("Starting Static Site Generation (SSG)...")

        try:
            # Recursively walk pages
            for root, _, files in os.walk(pages_root):
                for file in files:
                    if file.endswith((".py", ".html", ".asok")) and not file.startswith(
                        "__"
                    ):
                        full_path = os.path.join(root, file)
                        rel_path = os.path.relpath(full_path, pages_root)

                        # Convert to URL path format
                        # e.g., index.py -> /, about/page.html -> /about
                        url_path = rel_path.replace("\\", "/")
                        base, ext = os.path.splitext(url_path)

                        if base.endswith("/page"):
                            base = base[:-5]
                        elif base == "page":
                            base = ""
                        elif base == "index":
                            base = ""
                        elif base.endswith("/index"):
                            base = base[:-6]

                        route_path = "/" + base.strip("/")

                        # Check if it has dynamic parameters (braced segments like [slug])
                        is_dynamic = "[" in route_path and "]" in route_path

                        if not is_dynamic:
                            # Check if there is a companion python controller file in the same directory
                            py_file = None
                            if file.endswith((".html", ".asok")):
                                base_name, _ = os.path.splitext(file)
                                for py_ext in (".py", ".pyc"):
                                    test_py = os.path.join(root, base_name + py_ext)
                                    if os.path.isfile(test_py):
                                        py_file = test_py
                                        break

                            # If it is controlled by Python, only pre-render if configured for SSG/ISR
                            should_render = True
                            target_py = full_path if file.endswith((".py", ".pyc")) else py_file
                            if target_py:
                                try:
                                    module = self._load_module(target_py)
                                    should_render = (
                                        getattr(module, "SSG", False) is True
                                        or getattr(module, "REVALIDATE", None) is not None
                                    )
                                except Exception:
                                    should_render = False

                            if should_render:
                                cache_file = self._get_ssg_cache_file(route_path)
                                logger.info(f"  Pre-rendering static route {route_path}...")
                                try:
                                    self._generate_ssg_page_sync(
                                        route_path, full_path, cache_file
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"  Failed to pre-render static page {route_path}: {e}"
                                    )


                        else:
                            # Dynamic route: check if get_static_paths is defined
                            if file.endswith((".py", ".pyc")):
                                try:
                                    module = self._load_module(full_path)
                                    if hasattr(module, "get_static_paths"):
                                        get_paths_fn = getattr(module, "get_static_paths")
                                        paths = get_paths_fn()
                                        if inspect.iscoroutine(paths):
                                            paths = async_to_sync(paths)

                                        # paths should be a list of dicts, e.g. [{"slug": "hello"}, {"slug": "world"}]
                                        for p in paths:
                                            # Replace brackets in route_path with the parameter values
                                            rendered_route = route_path
                                            for k, v in p.items():
                                                # support both [name] and [name:type] formats
                                                rendered_route = re.sub(
                                                    rf"\[{k}(:\w+)?\]",
                                                    str(v),
                                                    rendered_route,
                                                )

                                            cache_file = self._get_ssg_cache_file(
                                                rendered_route
                                            )
                                            logger.info(
                                                f"  Pre-rendering dynamic route {rendered_route}..."
                                            )
                                            self._generate_ssg_page_sync(
                                                rendered_route, full_path, cache_file
                                            )
                                except Exception as e:
                                    logger.error(
                                        f"  Failed to pre-render dynamic page {route_path}: {e}"
                                    )
        finally:
            for p in added_paths:
                if p in sys.path:
                    sys.path.remove(p)
