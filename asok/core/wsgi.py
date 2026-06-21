from __future__ import annotations

import inspect
import json
import logging
import os
import secrets
import time
import traceback
import uuid
from typing import Any, Callable, Optional

from ..context import request_context, request_var
from ..exceptions import AbortException, RedirectException
from ..request import Request
from ..templates import render_template_string
from ..utils.minify import minify_html
from .smart_streamer import SmartStreamer

logger = logging.getLogger("asok.wsgi")


class _FinalResponseException(Exception):
    def __init__(self, body: Any, status_code: int, content_type: str = "text/html"):
        super().__init__()
        self.body = body
        self.status_code = status_code
        self.content_type = content_type


class _FinalRedirectException(Exception):
    def __init__(self, url: str, status_str: str, headers: list[tuple[str, str]]):
        super().__init__()
        self.url = url
        self.status_str = status_str
        self.headers = headers


_ADMIN_REDIRECTED = object()


class WSGIMixin:
    """Mixin class for Asok that handles the main WSGI entry point, middleware execution,
    static file serving, hot reload checks, and error rendering.
    """

    # ── Live reload (DEBUG only) ────────────────────────────

    _WATCH_IGNORE_DIRS = ("uploads", "__pycache__", ".git")
    _mtime_cache: float = 0
    _mtime_cache_ts: float = 0
    _MTIME_CACHE_TTL: float = 0.5

    def _get_src_mtime(self) -> float:
        now = time.monotonic()
        if now - self._mtime_cache_ts < self._MTIME_CACHE_TTL:
            return self._mtime_cache
        max_mtime = self._scan_root_mtime()
        max_mtime = self._scan_src_mtime(max_mtime)
        self._mtime_cache = max_mtime
        self._mtime_cache_ts = now
        return max_mtime

    def _scan_root_mtime(self) -> float:
        max_mtime = 0.0
        for f in (".env", "wsgi.py", "wsgi.pyc"):
            p = os.path.join(self.root_dir, f)
            if os.path.isfile(p):
                max_mtime = max(max_mtime, self._safe_mtime(p))
        return max_mtime

    def _scan_src_mtime(self, max_mtime: float) -> float:
        src_dir = os.path.join(self.root_dir, "src")
        if not os.path.isdir(src_dir):
            return max_mtime
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in self._WATCH_IGNORE_DIRS]
            max_mtime = self._max_mtime_in_files(root, files, max_mtime)
        return max_mtime

    def _max_mtime_in_files(self, root: str, files: list[str], current_max: float) -> float:
        for f in files:
            current_max = max(current_max, self._safe_mtime(os.path.join(root, f)))
        return current_max

    @staticmethod
    def _safe_mtime(path: str) -> float:
        try:
            return os.stat(path).st_mtime
        except OSError:
            return 0.0

    def _get_middleware_chain(self, core_layer: Callable) -> Callable:
        """Compose the user middleware handlers into a single callable chain."""
        if not self.middleware_handlers:
            return core_layer

        chain = core_layer
        for mw_handle in self._middleware_handlers_reversed:

            def mw_wrapper(req, mw=mw_handle, nxt=chain):
                return mw(req, nxt)

            chain = mw_wrapper
        return chain

    _CORS_METHODS = "GET, POST, PUT, DELETE, PATCH, OPTIONS"
    _CORS_HEADERS = "Content-Type, X-CSRF-Token, X-Block"

    def _preflight_cors_headers(self, origin: str, cors_origins: Any) -> list[tuple[str, str]]:
        allowed_origin, allow_cred = self._resolve_cors(origin, cors_origins)
        origin_header = allowed_origin or "*"
        headers = [
            ("Access-Control-Allow-Origin", origin_header),
            ("Access-Control-Allow-Methods", self._CORS_METHODS),
            ("Access-Control-Allow-Headers", self._CORS_HEADERS),
            ("Access-Control-Max-Age", "86400"),
        ]
        if allow_cred:
            headers.append(("Access-Control-Allow-Credentials", "true"))
        return headers

    def _handle_options_request(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        cors_origins = self.config.get("CORS_ORIGINS")
        if request.method != "OPTIONS" or not cors_origins:
            return None
        origin = environ.get("HTTP_ORIGIN", "")
        headers: list[tuple[str, str]] = []
        if cors_origins == "*" or self._cors_allowed(origin):
            headers.extend(self._preflight_cors_headers(origin, cors_origins))
        start_response("204 No Content", headers)
        return [b""]

    def _handle_reload_request(
        self, request: Request, start_response: Callable
    ) -> Optional[list[bytes]]:
        if request.path == "/__reload" and self.config.get("DEBUG"):
            mtime = self._get_src_mtime()
            body = str(mtime).encode()
            start_response(
                "200 OK",
                [("Content-Type", "text/plain"), ("Cache-Control", "no-cache")],
            )
            return [body]
        return None

    def _handle_admin_request(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        admin = getattr(self, "_admin", None)
        if not admin or not self._is_admin_path(request, admin):
            return None
        content_str = self._run_admin_dispatch(request, environ, admin, start_response)
        if content_str is _ADMIN_REDIRECTED:
            return self._last_admin_redirect_body
        return self._send_admin_body(request, content_str, environ, start_response)

    def _run_admin_dispatch(
        self, request: Request, environ: dict[str, Any],
        admin: Any, start_response: Callable,
    ) -> Any:
        try:
            return admin.dispatch(request)
        except RedirectException as redir:
            self._last_admin_redirect_body = self._admin_redirect_response(
                request, environ, redir, start_response
            )
            return _ADMIN_REDIRECTED
        except AbortException as abort:
            return self._admin_abort_response(request, abort)
        except Exception as e:
            return self._admin_exception_response(request, e)

    @staticmethod
    def _is_admin_path(request: Request, admin: Any) -> bool:
        return request.path == admin.prefix or request.path.startswith(admin.prefix + "/")

    def _admin_redirect_response(
        self,
        request: Request,
        environ: dict[str, Any],
        redir: RedirectException,
        start_response: Callable,
    ) -> list[bytes]:
        is_ajax = environ.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        if is_ajax and request._new_flashes:
            return self._ajax_redirect_with_flashes(request, environ, redir, start_response)
        headers = [("Location", redir.url)]
        headers += environ.get("asok.extra_headers", [])
        headers += self._cookie_headers(request, environ)
        start_response("302 Found", headers)
        return [b""]

    def _ajax_redirect_with_flashes(
        self,
        request: Request,
        environ: dict[str, Any],
        redir: RedirectException,
        start_response: Callable,
    ) -> list[bytes]:
        # AJAX 302s drop Set-Cookie before the redirected fetch sees it; return a
        # 200 + X-Redirect so the flash cookie survives the round-trip.
        import html as _html

        flash_html = "".join(
            f'<div class="flash-msg {_html.escape(f.get("category", "info"))}" data-ttl="6000">'
            f'<span>{_html.escape(f.get("message", ""))}</span></div>'
            for f in request._new_flashes
        )
        body = f'<template data-block="#flash-zone">{flash_html}</template>'
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("X-Redirect", redir.url),
        ]
        headers += environ.get("asok.extra_headers", [])
        headers += self._cookie_headers(request, environ)
        start_response("200 OK", headers)
        return [body.encode("utf-8")]

    def _admin_abort_response(self, request: Request, abort: AbortException) -> Any:
        request.status = Request._STATUS_MAP.get(
            abort.status, f"{abort.status} Unknown"
        )
        return self._render_error_page(request, abort.status, message=abort.message)

    def _admin_exception_response(self, request: Request, e: Exception) -> Any:
        from ..exceptions import SecurityError

        if isinstance(e, SecurityError):
            request.status = "403 Forbidden"
            return self._render_error_page(request, 403, message=str(e))
        error_id = str(uuid.uuid4())[:8]
        logger.error(
            "[ERROR-ID:%s] Admin Dispatch Exception: %s\n%s",
            error_id, str(e), traceback.format_exc(),
        )
        # SECURITY: never expose stack traces; server-side logs hold the details.
        request.status = "500 Internal Server Error"
        return self._render_error_page(
            request,
            500,
            message=f"An error occurred in the admin panel. Error ID: {error_id}",
        )

    def _send_admin_body(
        self,
        request: Request,
        content_str: Any,
        environ: dict[str, Any],
        start_response: Callable,
    ) -> list[bytes]:
        if "asok.binary_response" in environ:
            return self._send_admin_binary(request, environ, start_response)
        if "text/html" in request.content_type:
            content_str = self._inject_assets(
                content_str, request, getattr(request, "nonce", None)
            )
        output = str(content_str).encode("utf-8")
        headers = [("Content-Type", request.content_type)]
        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(
            request=request, nonce=getattr(request, "nonce", None)
        )
        headers += environ.get("asok.extra_headers", [])
        headers += request.response_headers
        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [output]

    def _send_admin_binary(
        self,
        request: Request,
        environ: dict[str, Any],
        start_response: Callable,
    ) -> list[bytes]:
        output = environ["asok.binary_response"]
        headers = [("Content-Type", request.content_type)]
        headers += environ.get("asok.extra_headers", [])
        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(request=request)
        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [output]

    def _handle_docs_request(
        self, request: Request, start_response: Callable
    ) -> Optional[list[bytes]]:
        if not self.config.get("DOCS", False):
            return None
        from ..api import handle_docs_request

        res = handle_docs_request(self, request)
        if not res:
            return None
        return self._send_api_response(request, res, start_response)

    def _handle_graphql_request(
        self, request: Request, start_response: Callable
    ) -> Optional[list[bytes]]:
        if request.path != "/graphql":
            return None
        if not self.config.get("GRAPHQL_ENABLED", False):
            return None
        from ..api.graphql import handle_graphql_request

        res = handle_graphql_request(self, request)
        if res is None:
            return None
        return self._send_api_response(request, res, start_response)

    def _send_api_response(
        self, request: Request, res: Any, start_response: Callable
    ) -> list[bytes]:
        output = self._encode_response(res)
        headers = [("Content-Type", request.content_type)]
        headers += self._cookie_headers(request, request.environ)
        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [output]

    @staticmethod
    def _encode_response(res: Any) -> bytes:
        if isinstance(res, bytes):
            return res
        if isinstance(res, str):
            return res.encode("utf-8")
        return str(res).encode("utf-8")

    _DISPATCH_METHODS = ("get", "post", "put", "delete", "patch", "head", "options")
    _REDIRECT_STATUS_MAP = {
        301: "301 Moved Permanently",
        302: "302 Found",
        303: "303 See Other",
        307: "307 Temporary Redirect",
    }

    def _dispatch_controller(self, request: Request, environ: dict[str, Any]) -> Any:
        page_file = self._resolve_request_page(request)
        if not page_file:
            return self._not_found_response(request)
        self._assign_page_metadata(request, page_file)
        try:
            return self._run_dispatch(request, page_file)
        except RedirectException as redir:
            raise self._build_final_redirect(request, environ, redir)
        except AbortException as abort:
            return self._abort_to_error_page(request, abort)
        except Exception as e:
            return self._handle_dispatch_exception(request, e)

    def _resolve_request_page(self, request: Request) -> Optional[str]:
        parts = [p for p in request.path.split("/") if p]
        page_file, route_params = self._resolve_route(parts, request=request)
        request.params.update(route_params)
        request._current_page_file = page_file
        return page_file

    def _run_dispatch(self, request: Request, page_file: str) -> Any:
        module = self._load_page_module(page_file)
        core_layer = self._make_core_layer(module, page_file)
        content_str = self._run_middleware_chain(request, module, core_layer)
        return self._normalize_error_body(request, content_str)

    def _assign_page_metadata(self, request: Request, page_file: str) -> None:
        try:
            request.page_id = self._compute_page_id(page_file)
            self._attach_scoped_assets(request, page_file)
        except Exception:
            request.page_id = "unknown"

    def _compute_page_id(self, page_file: str) -> str:
        pages_root = os.path.join(self.root_dir, self.dirs["PAGES"])
        rel = os.path.relpath(page_file, pages_root)
        base_name = os.path.splitext(rel)[0]
        if base_name == self.config["INDEX"] or base_name.endswith(
            os.sep + self.config["INDEX"]
        ):
            base_name = os.path.dirname(rel) or "index"
        page_id = base_name.replace(os.sep, "-").replace(".", "-").strip("-")
        return page_id or "index"

    @staticmethod
    def _attach_scoped_assets(request: Request, page_file: str) -> None:
        base_path = os.path.splitext(page_file)[0]
        for ext in ("css", "js"):
            p = f"{base_path}.{ext}"
            if os.path.isfile(p):
                request.scoped_assets[ext] = p

    def _not_found_response(self, request: Request) -> Any:
        body = self._render_error_page(request, 404)
        if inspect.isgenerator(body):
            return body
        body = self._inject_assets(body, request, getattr(request, "nonce", ""))
        raise _FinalResponseException(body, 404)

    def _load_page_module(self, page_file: str) -> Any:
        if page_file.endswith(".py") or page_file.endswith(".pyc"):
            return self._load_module(page_file)
        return None

    def _make_core_layer(self, module: Any, page_file: str) -> Callable:
        def core_layer(req: Request) -> Any:
            csrf_failure = self._enforce_csrf(req)
            if csrf_failure is not None:
                return csrf_failure
            if module:
                return self._dispatch_module(req, module, page_file)
            return self._dispatch_template(req, page_file)
        return core_layer

    def _enforce_csrf(self, req: Request) -> Any:
        if not self.config.get("CSRF") or req.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None
        try:
            req.verify_csrf()
        except Exception:
            return self._handle_csrf_failure(req)
        req.csrf_token_value = secrets.token_hex(32)
        return None

    @staticmethod
    def _handle_csrf_failure(req: Request) -> Any:
        logger.warning(
            "[CSRF FAIL] %s %s from %s (UA: %s)",
            req.method, req.path, req.ip,
            req.headers.get("User-Agent", "unknown")[:120],
        )
        if req.path.startswith("/api"):
            return req.api_error("CSRF validation failed.", status=403)
        from ..exceptions import SecurityError

        raise SecurityError("CSRF validation failed.")

    def _dispatch_module(self, req: Request, module: Any, page_file: str) -> Any:
        self._apply_api_versioning(req, module)
        supported = self._collect_supported_methods(module)
        if req.method == "POST":
            res = self._dispatch_action(req, module, page_file)
            if res is not None:
                return res
        return self._dispatch_module_handler(req, module, page_file, supported)

    def _dispatch_module_handler(
        self, req: Request, module: Any, page_file: str, supported: list[str]
    ) -> Any:
        method_func = getattr(module, req.method.lower(), None)
        if callable(method_func):
            return self._call_handler(
                req, method_func, page_file, f"Method function '{req.method.lower()}'"
            )
        if hasattr(module, "render"):
            return self._dispatch_render(req, module, page_file, supported)
        if hasattr(module, "CONTENT"):
            return module.CONTENT
        return self._fallback_no_handler(req, supported)

    @staticmethod
    def _fallback_no_handler(req: Request, supported: list[str]) -> str:
        if supported and req.method not in supported:
            req.method_not_allowed(supported)
        req.status = "404 Not Found"
        return "<h1>404 Not Found</h1><p>The requested route does not provide a valid handler.</p>"

    def _apply_api_versioning(self, req: Request, module: Any) -> None:
        deprecation, sunset = self._api_version_metadata(req, module)
        if deprecation:
            req.response_headers.append(("Deprecation", "true"))
        if sunset:
            req.response_headers.append(("Sunset", self._format_sunset(sunset)))

    @classmethod
    def _api_version_metadata(cls, req: Request, module: Any) -> tuple[bool, Optional[str]]:
        deprecation = bool(getattr(module, "__api_deprecated__", False))
        sunset = getattr(module, "__api_sunset__", None)
        meta = cls._api_version_meta(req, module)
        if meta:
            deprecation = deprecation or meta.deprecated
            sunset = sunset or meta.sunset
        return deprecation, sunset

    @staticmethod
    def _api_version_meta(req: Request, module: Any) -> Any:
        handler = getattr(module, req.method.lower(), None) or getattr(module, "render", None)
        if not handler:
            return None
        return getattr(handler, "_asok_api_version", None)

    @staticmethod
    def _format_sunset(sunset: str) -> str:
        try:
            import email.utils
            from datetime import datetime

            dt = datetime.fromisoformat(sunset.replace("Z", "+00:00"))
            return email.utils.format_datetime(dt, usegmt=True)
        except Exception:
            return sunset

    def _collect_supported_methods(self, module: Any) -> list[str]:
        supported: list[str] = list(self._explicit_methods(module))
        for m in self._DISPATCH_METHODS:
            upper = m.upper()
            if callable(getattr(module, m, None)) and upper not in supported:
                supported.append(upper)
        return supported

    @staticmethod
    def _explicit_methods(module: Any):
        explicit = getattr(module, "METHODS", None)
        if isinstance(explicit, (list, tuple)):
            return [m.upper() for m in explicit]
        return []

    def _dispatch_action(self, req: Request, module: Any, page_file: str) -> Any:
        action_name = self._safe_action_name(req)
        self._validate_block_header(req)
        if not action_name:
            return None
        action_func = getattr(module, f"action_{action_name}", None)
        if not callable(action_func):
            return None
        if self.config.get("CSRF"):
            req.verify_csrf()
        return self._call_handler(
            req, action_func, page_file,
            f"Action handler 'action_{action_name}'",
            extra="Ensure your action returns request.html(), request.json(), or calls request.redirect().",
        )

    @classmethod
    def _safe_action_name(cls, req: Request) -> Optional[str]:
        name = req.form.get("_action") or req.args.get("_action") or req.args.get("action")
        if name and cls._is_valid_action_name(name):
            return name
        return None

    @staticmethod
    def _is_valid_action_name(name: str) -> bool:
        if name.startswith("_"):
            return False
        return name.replace("_", "").replace("-", "").isalnum()

    def _validate_block_header(self, req: Request) -> None:
        block_header = req.environ.get("HTTP_X_BLOCK")
        if not block_header:
            return
        for raw in block_header.split(","):
            self._validate_block_name(req, raw.strip())

    def _validate_block_name(self, req: Request, bname: str) -> None:
        if not bname:
            return
        if bname.startswith("#") or not bname.replace("_", "").replace("-", "").isalnum():
            msg = (
                f"Invalid block name format: '{bname}'. Only alphanumeric characters, "
                "underscores and dashes are allowed (no # prefix)."
            )
            logger.warning(f"CONSISTENCY ERROR: {msg} (from {req.ip})")
            req.abort(400, msg)

    def _call_handler(
        self, req: Request, handler: Callable, page_file: str, label: str, extra: str = ""
    ) -> Any:
        res = handler(req)
        if res is None:
            message = f"{label} in {page_file} returned None."
            if extra:
                message += f" {extra}"
            req.abort(500, message)
        return self._resolve_if_coro(req, res)

    def _dispatch_render(
        self, req: Request, module: Any, page_file: str, supported: list[str]
    ) -> Any:
        res = module.render(req)
        if res is None:
            if supported and req.method not in supported:
                req.method_not_allowed(supported)
            req.abort(500, f"render() in {page_file} returned None. Check your logic.")
        return self._resolve_if_coro(req, res)

    def _dispatch_template(self, req: Request, page_file: str) -> Any:
        content_raw = self._read_template(page_file)
        tpl_ctx = {
            "request": req,
            "__": req.__,
            "static": req.static,
            "get_flashed_messages": req.get_flashed_messages,
        }
        return render_template_string(content_raw, tpl_ctx, root_dir=self._tpl_root)

    @staticmethod
    def _resolve_if_coro(req: Request, r: Any) -> Any:
        if not inspect.iscoroutine(r):
            return r
        if req.environ.get("asok.asgi"):
            return r
        from .asgi import async_to_sync

        return async_to_sync(r)

    def _run_middleware_chain(self, request: Request, module: Any, core_layer: Callable) -> Any:
        if self._asyncio_loop_running():
            return self._run_in_async_loop(request, core_layer)
        if self._needs_async_chain(module, request):
            return self._run_async_chain_sync(request, core_layer)
        return self._run_sync_chain(request, core_layer)

    @staticmethod
    def _asyncio_loop_running() -> bool:
        import asyncio

        try:
            return asyncio.get_running_loop().is_running()
        except RuntimeError:
            return False

    def _run_in_async_loop(self, request: Request, core_layer: Callable) -> Any:
        chain = self._get_async_middleware_chain(core_layer)
        with request_context(request):
            return chain(request)

    def _run_async_chain_sync(self, request: Request, core_layer: Callable) -> Any:
        chain = self._get_async_middleware_chain(core_layer)
        from .asgi import async_to_sync

        with request_context(request):
            coro = chain(request)
            return async_to_sync(coro)

    def _run_sync_chain(self, request: Request, core_layer: Callable) -> Any:
        chain = self._get_middleware_chain(core_layer)
        with request_context(request):
            return chain(request)

    def _needs_async_chain(self, module: Any, request: Request) -> bool:
        if self._has_async_middleware:
            return True
        return self._module_is_async(module, request)

    def _module_is_async(self, module: Any, request: Request) -> bool:
        if not module:
            return False
        if self._async_method_handler(module, request):
            return True
        if request.method == "POST" and self._async_post_action(module, request):
            return True
        return self._async_render(module)

    @staticmethod
    def _async_method_handler(module: Any, request: Request) -> bool:
        handler = getattr(module, request.method.lower(), None)
        return callable(handler) and inspect.iscoroutinefunction(handler)

    @staticmethod
    def _async_render(module: Any) -> bool:
        render = getattr(module, "render", None)
        return callable(render) and inspect.iscoroutinefunction(render)

    @staticmethod
    def _async_post_action(module: Any, request: Request) -> bool:
        action_name = (
            request.form.get("_action")
            or request.args.get("_action")
            or request.args.get("action")
        )
        if not action_name:
            return False
        action_func = getattr(module, f"action_{action_name}", None)
        return callable(action_func) and inspect.iscoroutinefunction(action_func)

    def _normalize_error_body(self, request: Request, content_str: Any) -> Any:
        status_code = request.status.split(" ")[0]
        if not status_code.startswith(("4", "5")):
            return content_str
        if self._is_default_error_body(content_str):
            return self._render_error_page(request, int(status_code))
        return content_str

    @staticmethod
    def _is_default_error_body(content_str: Any) -> bool:
        if isinstance(content_str, str):
            return "<h1>" in content_str
        return True

    def _build_final_redirect(
        self, request: Request, environ: dict[str, Any], redir: RedirectException
    ) -> _FinalRedirectException:
        self._maybe_record_redirect_stats(request)
        headers = [("Location", redir.url)]
        headers += self._cookie_headers(request, environ)
        status_str = self._REDIRECT_STATUS_MAP.get(redir.status, f"{redir.status} Found")
        return _FinalRedirectException(redir.url, status_str, headers)

    def _maybe_record_redirect_stats(self, request: Request) -> None:
        if not self._show_toolbar() or not hasattr(request, "_asok_sql_log"):
            return
        self._record_redirect_stats_safe(request, request._asok_sql_log)

    def _show_toolbar(self) -> bool:
        if "TOOLBAR" in self.config:
            return self._coerce_bool(self.config.get("TOOLBAR"))
        return self._coerce_bool(self.config.get("DEBUG"))

    @staticmethod
    def _coerce_bool(val: Any) -> bool:
        if isinstance(val, str):
            return val.lower() in ("true", "yes", "1", "on")
        return bool(val)

    def _abort_to_error_page(self, request: Request, abort: AbortException) -> Any:
        request.status = Request._STATUS_MAP.get(abort.status, f"{abort.status} Unknown")
        return self._render_error_page(request, abort.status, message=abort.message)

    def _handle_dispatch_exception(self, request: Request, e: Exception) -> Any:
        from ..exceptions import (
            AsokException,
            SecurityError,
            TemplateError,
            ValidationError,
        )

        mapped = self._map_known_exception(request, e, SecurityError, ValidationError, TemplateError, AsokException)
        if mapped is not None:
            return mapped
        return self._handle_unknown_exception(request, e)

    def _map_known_exception(
        self, request: Request, e: Exception,
        SecurityError, ValidationError, TemplateError, AsokException,
    ) -> Any:
        if isinstance(e, SecurityError):
            request.status = "403 Forbidden"
            return self._render_error_page(request, 403, message=str(e))
        if isinstance(e, ValidationError):
            request.status = "400 Bad Request"
            return self._render_error_page(request, 400, message=str(e))
        if isinstance(e, TemplateError):
            request.status = "500 Internal Server Error"
            return self._render_error_page(request, 500, message=f"Template Error: {e}")
        if isinstance(e, AsokException):
            request.status = "500 Internal Server Error"
            return self._render_error_page(request, 500, message=str(e))
        return None

    def _handle_unknown_exception(self, request: Request, e: Exception) -> Any:
        error_id = str(uuid.uuid4())[:8]
        logger.error(
            "[ERROR-ID:%s] Unhandled Exception: %s\n%s",
            error_id, str(e), traceback.format_exc(),
        )
        # SECURITY: never reveal traces; surface only the error ID to the client.
        body = self._render_error_page(
            request, 500,
            message=(
                f"An unexpected error occurred. Error ID: {error_id}. "
                "Please contact support with this ID if the problem persists."
            ),
        )
        body = self._inject_assets(body, request, getattr(request, "nonce", ""))
        raise _FinalResponseException(body, 500)

    def _finalize_response(
        self,
        request: Request,
        content_str: Any,
        environ: dict[str, Any],
        is_head: bool,
        start_response: Callable,
    ) -> list[bytes]:
        if "asok.stream_file" in environ:
            return self._finalize_stream_file(request, environ, is_head, start_response)
        if "asok.binary_response" in environ:
            return self._finalize_binary(request, environ, is_head, start_response)
        headers = self._base_response_headers(request, environ)
        if inspect.isgenerator(content_str):
            return self._finalize_streaming(request, content_str, environ, headers, start_response)
        content_str = self._maybe_inject_and_minify(request, content_str)
        output = str(content_str).encode("utf-8")
        output = self._maybe_gzip(output, environ, headers)
        self._apply_asok_blocks_headers(request, headers)
        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [b""] if is_head else [output]

    def _finalize_stream_file(
        self, request: Request, environ: dict[str, Any],
        is_head: bool, start_response: Callable,
    ) -> Any:
        stream_path = environ["asok.stream_file"]
        headers = [("Content-Type", request.content_type)]
        headers += environ.get("asok.extra_headers", [])
        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(request=request)
        start_response(request.status, headers)
        if is_head:
            return [b""]
        return self._file_iter(stream_path)

    @staticmethod
    def _file_iter(path: str, chunk_size: int = 65536):
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except (OSError, IOError) as e:
            logger.error("File Streaming Error: %s", str(e))
            return

    def _finalize_binary(
        self, request: Request, environ: dict[str, Any],
        is_head: bool, start_response: Callable,
    ) -> list[bytes]:
        output = environ["asok.binary_response"]
        headers = [("Content-Type", request.content_type)]
        headers += environ.get("asok.extra_headers", [])
        headers += self._cookie_headers(request, environ)
        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [b""] if is_head else [output]

    def _base_response_headers(
        self, request: Request, environ: dict[str, Any]
    ) -> list[tuple[str, str]]:
        headers = [("Content-Type", request.content_type)]
        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(
            request=request, nonce=getattr(request, "nonce", None)
        )
        headers += environ.get("asok.extra_headers", [])
        headers += request.response_headers
        cors_origins = self.config.get("CORS_ORIGINS")
        origin = environ.get("HTTP_ORIGIN", "")

        self._expose_csrf_token_if_needed(headers, request, origin, cors_origins)
        self._append_cors_headers(headers, environ)
        return headers

    def _expose_csrf_token_if_needed(
        self, headers: list[tuple[str, str]], request: Request, origin: str, cors_origins: Any
    ) -> None:
        if self._should_expose_csrf(origin, request, cors_origins):
            headers.append(("X-CSRF-Token", request.csrf_token_value))
            headers.append(("Access-Control-Expose-Headers", "X-CSRF-Token"))

    def _should_expose_csrf(self, origin: str, request: Request, cors_origins: Any) -> bool:
        if not origin:
            return True
        if self._is_same_origin(origin, request):
            return True
        if not cors_origins or cors_origins == "*":
            return False
        return self._cors_allowed(origin)

    def _effective_port(self, parsed) -> int:
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    def _is_same_origin(self, origin: str, request: Request) -> bool:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(origin)
            request_host = request.environ.get("HTTP_HOST", "localhost")
            parsed_req = urlparse(f"{request.scheme}://{request_host}")
            return (
                parsed.scheme == parsed_req.scheme
                and parsed.hostname == parsed_req.hostname
                and self._effective_port(parsed) == self._effective_port(parsed_req)
            )
        except Exception:
            return False

    def _resolve_cors(self, origin: str, cors_origins: Any) -> tuple[Optional[str], bool]:
        if cors_origins == "*":
            return self._resolve_wildcard_cors(origin)
        if self._cors_allowed(origin) and origin:
            return origin, True
        return None, False

    def _resolve_wildcard_cors(self, origin: str) -> tuple[Optional[str], bool]:
        if self.config.get("DEBUG") and origin:
            return origin, True
        return "*", False

    def _append_cors_headers(
        self, headers: list[tuple[str, str]], environ: dict[str, Any]
    ) -> None:
        cors_origins = self.config.get("CORS_ORIGINS")
        if not cors_origins:
            return
        origin = environ.get("HTTP_ORIGIN", "")
        allowed_origin, allow_cred = self._resolve_cors(origin, cors_origins)
        if not allowed_origin:
            return
        headers.append(("Access-Control-Allow-Origin", allowed_origin))
        if allow_cred:
            headers.append(("Access-Control-Allow-Credentials", "true"))
        headers.append(("Access-Control-Allow-Methods", self._CORS_METHODS))
        headers.append(("Access-Control-Allow-Headers", self._CORS_HEADERS))

    def _finalize_streaming(
        self, request: Request, content_str: Any, environ: dict[str, Any],
        headers: list[tuple[str, str]], start_response: Callable,
    ) -> Any:
        if self._gzip_accepted(environ):
            headers.append(("Content-Encoding", "gzip"))
            headers.append(("Vary", "Accept-Encoding"))
        block_header = environ.get("HTTP_X_BLOCK")
        if block_header:
            headers.append(("X-Asok-Blocks", block_header))
            self._expose_header(headers, "X-Asok-Blocks")
        start_response(request.status, headers)
        return SmartStreamer(content_str, request, self)

    def _gzip_accepted(self, environ: dict[str, Any]) -> bool:
        if not self.config.get("GZIP", False):
            return False
        return "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "").lower()

    def _maybe_inject_and_minify(self, request: Request, content_str: Any) -> Any:
        if "text/html" not in request.content_type:
            return content_str
        content_str = self._inject_assets(
            content_str, request, getattr(request, "nonce", None)
        )
        if self._should_minify_html():
            content_str = minify_html(str(content_str))
        return content_str

    def _should_minify_html(self) -> bool:
        should_minify = self.config.get("HTML_MINIFY")
        if should_minify is None:
            return not self.config.get("DEBUG")
        return bool(should_minify)

    def _maybe_gzip(
        self, output: bytes, environ: dict[str, Any], headers: list[tuple[str, str]]
    ) -> bytes:
        if not self._gzip_accepted(environ):
            return output
        if len(output) <= self.config.get("GZIP_MIN_SIZE", 500):
            return output
        import gzip as gzip_mod
        import io

        buf = io.BytesIO()
        with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
            f.write(output)
        headers.append(("Content-Encoding", "gzip"))
        headers.append(("Vary", "Accept-Encoding"))
        return buf.getvalue()

    def _apply_asok_blocks_headers(
        self, request: Request, headers: list[tuple[str, str]]
    ) -> None:
        blocks = getattr(request, "_asok_blocks", None)
        if not blocks:
            return
        headers.append(("X-Asok-Blocks", ",".join(blocks)))
        self._maybe_attach_toolbar_headers(request, headers)
        self._expose_header(headers, "X-Asok-Blocks")

    def _maybe_attach_toolbar_headers(
        self, request: Request, headers: list[tuple[str, str]]
    ) -> None:
        if not (self.config.get("DEBUG") or self.config.get("TOOLBAR")):
            return
        if not hasattr(request, "_asok_sql_log"):
            return
        sql_log = request._asok_sql_log
        headers.append(("X-Asok-SQL-Count", str(len(sql_log))))
        try:
            headers.append(("X-Asok-SQL-Log", json.dumps(sql_log)))
        except Exception:
            pass
        self._record_redirect_stats_safe(request, sql_log)

    def _record_redirect_stats_safe(self, request: Request, sql_log: Any) -> None:
        try:
            request.session["_asok_redir_stats"] = {
                "path": request.path,
                "method": request.method,
                "args": dict(request.args),
                "form": dict(request.form),
                "sql_log": sql_log,
            }
            sess = request._session
            if sess is not None and hasattr(self, "_session_store"):
                self._session_store.save(sess.sid, sess)
        except Exception:
            pass

    _EXPOSE_HEADER_KEY = "Access-Control-Expose-Headers"

    @classmethod
    def _expose_header(cls, headers: list[tuple[str, str]], name: str) -> None:
        """Append name to Access-Control-Expose-Headers in a single pass."""
        key = cls._EXPOSE_HEADER_KEY
        for i, (k, v) in enumerate(headers):
            if k == key:
                headers[i] = (key, f"{v}, {name}")
                return
        headers.append((key, name))

    _STATIC_PREFIXES = ("/css/", "/js/", "/images/", "/uploads/")
    _NOISE_PATHS = ("/__reload", "/favicon.ico")

    def _wsgi_call(
        self, environ: dict[str, Any], start_response: Callable
    ) -> list[bytes]:
        """Main WSGI entry point for the Asok framework."""
        self._prepare_wsgi_environ(environ)
        self._log_request(environ)
        request = Request(environ)
        token = request_var.set(request)
        try:
            return self._handle_request_lifecycle(request, environ, start_response)
        finally:
            self._wsgi_teardown(request, token)

    def _prepare_wsgi_environ(self, environ: dict[str, Any]) -> None:
        environ["asok.root"] = self.root_dir
        environ["asok.app"] = self

    def _log_request(self, environ: dict[str, Any]) -> None:
        path = environ.get("PATH_INFO", "")
        if self._is_static_or_noise(path):
            return
        logger.info(
            "[%s] %s (DEBUG=%s)",
            environ.get("REQUEST_METHOD"), path, self.config.get("DEBUG"),
        )

    @classmethod
    def _is_static_or_noise(cls, path: str) -> bool:
        if path.startswith(cls._STATIC_PREFIXES):
            return True
        return path in cls._NOISE_PATHS or path.startswith("/.well-known/")

    def _handle_request_lifecycle(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> list[bytes]:
        from .signals import request_started

        request_started.send(self, request=request)
        self._init_request_nonce(request)
        is_head = self._normalize_head(request)
        if getattr(request, "_body_rejected", False):
            start_response("413 Payload Too Large", [("Content-Type", "text/plain")])
            return [b"Request body too large"]
        res = self._run_specialized_handlers(request, environ, start_response)
        if res is not None:
            return res
        _ = request.session
        return self._dispatch_and_finalize(request, environ, is_head, start_response)

    def _init_request_nonce(self, request: Request) -> None:
        # SECURITY: 128 bits of entropy (CSP Level 3 minimum), base64url ~22 chars.
        request._nonce = secrets.token_urlsafe(16)

    @staticmethod
    def _normalize_head(request: Request) -> bool:
        if request.method == "HEAD":
            request.method = "GET"
            return True
        return False

    def _run_specialized_handlers(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        res = self._handle_options_request(request, environ, start_response)
        if res is not None:
            return res
        res = self._handle_health_check(request, start_response)
        if res is not None:
            return res
        for handler in (
            lambda: self._handle_reload_request(request, start_response),
            lambda: self._handle_admin_request(request, environ, start_response),
            lambda: self._handle_docs_request(request, start_response),
            lambda: self._handle_graphql_request(request, start_response),
            lambda: self._handle_static_request(request, environ, start_response),
            lambda: self._handle_ssg_isr_request(request, environ, start_response),
        ):
            res = handler()
            if res is not None:
                return res
        return None

    @staticmethod
    def _handle_health_check(
        request: Request, start_response: Callable
    ) -> Optional[list[bytes]]:
        if request.path != "/__health":
            return None
        body = b'{"status":"ok"}'
        start_response(
            "200 OK",
            [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _dispatch_and_finalize(
        self, request: Request, environ: dict[str, Any],
        is_head: bool, start_response: Callable,
    ) -> list[bytes]:
        try:
            result = self._dispatch_controller(request, environ)
        except _FinalResponseException as fre:
            return self._send_final_response(fre, start_response)
        except _FinalRedirectException as frde:
            start_response(frde.status_str, frde.headers)
            return [b""]
        except Exception as e:
            return self._send_wsgi_error(request, e, start_response)
        return self._finalize_response(request, result, environ, is_head, start_response)

    @staticmethod
    def _send_final_response(
        fre: _FinalResponseException, start_response: Callable
    ) -> list[bytes]:
        status_str = Request._STATUS_MAP.get(
            fre.status_code, f"{fre.status_code} Unknown"
        )
        start_response(
            status_str, [("Content-Type", f"{fre.content_type}; charset=utf-8")]
        )
        body = fre.body.encode("utf-8") if isinstance(fre.body, str) else fre.body
        return [body]

    def _send_wsgi_error(
        self, request: Request, e: Exception, start_response: Callable
    ) -> list[bytes]:
        error_id = str(uuid.uuid4())[:8]
        logger.error(
            "[ERROR-ID:%s] Unhandled Exception in WSGI: %s\n%s",
            error_id, str(e), traceback.format_exc(),
        )
        # SECURITY: never expose traces to the client; logs hold details.
        error_page = self._render_error_page(
            request, 500, message="An internal error occurred."
        )
        start_response(
            "500 Internal Server Error",
            [("Content-Type", "text/html; charset=utf-8")],
        )
        body = error_page.encode("utf-8") if isinstance(error_page, str) else error_page
        return [body]

    def _wsgi_teardown(self, request: Request, token: Any) -> None:
        from ..orm import close_all_db_connections
        from .signals import request_finished

        try:
            request_finished.send(self, request=request)
        except Exception:
            pass
        close_all_db_connections()
        request_var.reset(token)
