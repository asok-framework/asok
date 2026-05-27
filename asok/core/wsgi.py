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

        max_mtime = 0.0

        for f in [".env", "wsgi.py", "wsgi.pyc"]:
            p = os.path.join(self.root_dir, f)
            if os.path.isfile(p):
                try:
                    m = os.stat(p).st_mtime
                    if m > max_mtime:
                        max_mtime = m
                except OSError:
                    pass

        src_dir = os.path.join(self.root_dir, "src")
        if not os.path.isdir(src_dir):
            return max_mtime
        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in self._WATCH_IGNORE_DIRS]
            for f in files:
                try:
                    mtime = os.stat(os.path.join(root, f)).st_mtime
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
        self._mtime_cache = max_mtime
        self._mtime_cache_ts = now
        return max_mtime

    def _get_middleware_chain(self, core_layer: Callable) -> Callable:
        """Compose the user middleware handlers into a single callable chain."""
        if not self.middleware_handlers:
            return core_layer

        chain = core_layer
        for mw_handle in reversed(self.middleware_handlers):

            def mw_wrapper(req, mw=mw_handle, nxt=chain):
                return mw(req, nxt)

            chain = mw_wrapper
        return chain

    def _handle_options_request(
        self, request: Request, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        cors_origins = self.config.get("CORS_ORIGINS")
        if request.method == "OPTIONS" and cors_origins:
            origin = environ.get("HTTP_ORIGIN", "")
            headers = []
            if cors_origins == "*" or self._cors_allowed(origin):
                headers.append(("Access-Control-Allow-Origin", origin or "*"))
                headers.append(("Access-Control-Allow-Credentials", "true"))
                headers.append(
                    (
                        "Access-Control-Allow-Methods",
                        "GET, POST, PUT, DELETE, PATCH, OPTIONS",
                    )
                )
                headers.append(
                    (
                        "Access-Control-Allow-Headers",
                        "Content-Type, X-CSRF-Token, X-Block",
                    )
                )
                headers.append(("Access-Control-Max-Age", "86400"))
            start_response("204 No Content", headers)
            return [b""]
        return None

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
        if admin and (
            request.path == admin.prefix or request.path.startswith(admin.prefix + "/")
        ):
            try:
                content_str = admin.dispatch(request)
            except RedirectException as redir:
                headers = [("Location", redir.url)]
                # Include extra headers (e.g., language cookie from admin)
                headers += environ.get("asok.extra_headers", [])
                headers += self._cookie_headers(request, environ)
                start_response("302 Found", headers)
                return [b""]
            except AbortException as abort:
                request.status = Request._STATUS_MAP.get(
                    abort.status, f"{abort.status} Unknown"
                )
                content_str = self._render_error_page(
                    request, abort.status, message=abort.message
                )
            except Exception as e:
                from ..exceptions import SecurityError

                if isinstance(e, SecurityError):
                    request.status = "403 Forbidden"
                    content_str = self._render_error_page(request, 403, message=str(e))
                else:
                    error_id = str(uuid.uuid4())[:8]

                    logger.error(
                        "[ERROR-ID:%s] Admin Dispatch Exception: %s\n%s",
                        error_id,
                        str(e),
                        traceback.format_exc(),
                    )
                    # SECURITY: Never expose stack traces to client, even in DEBUG mode
                    # Full error details are logged above for server-side debugging
                    request.status = "500 Internal Server Error"
                    content_str = self._render_error_page(
                        request,
                        500,
                        message=f"An error occurred in the admin panel. Error ID: {error_id}",
                    )
            if "asok.binary_response" in environ:
                output = environ["asok.binary_response"]
                headers = [("Content-Type", request.content_type)]
                headers += environ.get("asok.extra_headers", [])
                headers += self._cookie_headers(request, environ)
                headers.append(("Content-Length", str(len(output))))
                start_response(request.status, headers)
                return [output]
            output = str(content_str).encode("utf-8")
            headers = [("Content-Type", request.content_type)]
            headers += self._cookie_headers(request, environ)
            headers.append(("Content-Length", str(len(output))))
            start_response(request.status, headers)
            return [output]
        return None

    def _handle_docs_request(
        self, request: Request, start_response: Callable
    ) -> Optional[list[bytes]]:
        if self.config.get("DOCS", False):
            from ..api import handle_docs_request

            res = handle_docs_request(self, request)
            if res:
                if isinstance(res, bytes):
                    output = res
                elif isinstance(res, str):
                    output = res.encode("utf-8")
                else:
                    output = str(res).encode("utf-8")
                headers = [("Content-Type", request.content_type)]
                headers += self._cookie_headers(request, request.environ)
                headers.append(("Content-Length", str(len(output))))
                start_response(request.status, headers)
                return [output]
        return None

    def _dispatch_controller(self, request: Request, environ: dict[str, Any]) -> Any:
        parts = [p for p in request.path.split("/") if p]
        page_file, route_params = self._resolve_route(parts)

        request.params.update(route_params)
        request._current_page_file = page_file

        if page_file:
            try:
                pages_root = os.path.join(self.root_dir, self.dirs["PAGES"])
                rel = os.path.relpath(page_file, pages_root)
                base_name = os.path.splitext(rel)[0]

                if base_name == self.config["INDEX"] or base_name.endswith(
                    os.sep + self.config["INDEX"]
                ):
                    base_name = os.path.dirname(rel) or "index"

                request.page_id = (
                    base_name.replace(os.sep, "-").replace(".", "-").strip("-")
                )
                if not request.page_id:
                    request.page_id = "index"

                base_path = os.path.splitext(page_file)[0]
                for ext in ("css", "js"):
                    p = f"{base_path}.{ext}"
                    if os.path.isfile(p):
                        request.scoped_assets[ext] = p
            except Exception:
                request.page_id = "unknown"

        if not page_file:
            body = self._render_error_page(request, 404)
            if inspect.isgenerator(body):
                return body

            body = self._inject_assets(body, request, getattr(request, "nonce", ""))
            raise _FinalResponseException(body, 404)

        content_str = ""
        try:
            module = None
            if page_file.endswith(".py") or page_file.endswith(".pyc"):
                module = self._load_module(page_file)

            tpl_root = self._tpl_root

            def core_layer(req):

                def resolve_if_coro(r):
                    if inspect.iscoroutine(r):
                        if req.environ.get("asok.asgi"):
                            return r
                        else:
                            from .asgi import async_to_sync

                            return async_to_sync(r)
                    return r

                if self.config.get("CSRF") and req.method in (
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                ):
                    try:
                        req.verify_csrf()
                    except Exception:
                        logger.warning(
                            "[CSRF FAIL] %s %s from %s (UA: %s)",
                            req.method,
                            req.path,
                            req.ip,
                            req.headers.get("User-Agent", "unknown")[:120],
                        )
                        if req.path.startswith("/api"):
                            return req.api_error("CSRF validation failed.", status=403)

                        from ..exceptions import SecurityError

                        raise SecurityError("CSRF validation failed.")

                    req.csrf_token_value = secrets.token_hex(32)

                if module:
                    supported = []
                    if hasattr(module, "METHODS") and isinstance(
                        module.METHODS, (list, tuple)
                    ):
                        supported.extend([m.upper() for m in module.METHODS])
                    for m in [
                        "get",
                        "post",
                        "put",
                        "delete",
                        "patch",
                        "head",
                        "options",
                    ]:
                        if hasattr(module, m) and callable(getattr(module, m)):
                            if m.upper() not in supported:
                                supported.append(m.upper())

                    if req.method == "POST":
                        action_name = (
                            req.form.get("_action")
                            or req.args.get("_action")
                            or req.args.get("action")
                        )
                        if action_name:
                            if (
                                not action_name.replace("_", "")
                                .replace("-", "")
                                .isalnum()
                            ):
                                action_name = None
                            elif action_name.startswith("_"):
                                action_name = None

                        block_header = req.environ.get("HTTP_X_BLOCK")
                        if block_header:
                            names = [b.strip() for b in block_header.split(",")]
                            for bname in names:
                                if not bname:
                                    continue
                                if bname.startswith("#") or not (
                                    bname.replace("_", "").replace("-", "").isalnum()
                                ):
                                    msg = f"Invalid block name format: '{bname}'. Only alphanumeric characters, underscores and dashes are allowed (no # prefix)."
                                    logger.warning(
                                        f"CONSISTENCY ERROR: {msg} (from {req.ip})"
                                    )
                                    req.abort(400, msg)

                        if action_name:
                            action_func = getattr(module, f"action_{action_name}", None)
                            if callable(action_func):
                                req.verify_csrf()
                                res = action_func(req)
                                if res is None:
                                    req.abort(
                                        500,
                                        f"Action handler 'action_{action_name}' in {page_file} returned None. "
                                        "Ensure your action returns request.html(), request.json(), or calls request.redirect().",
                                    )
                                return resolve_if_coro(res)

                    method_func = getattr(module, req.method.lower(), None)
                    if callable(method_func):
                        res = method_func(req)
                        if res is None:
                            req.abort(
                                500,
                                f"Method function '{req.method.lower()}' in {page_file} returned None.",
                            )
                        return resolve_if_coro(res)

                    if hasattr(module, "render"):
                        res = module.render(req)
                        if res is None:
                            if supported and req.method not in supported:
                                req.method_not_allowed(supported)
                            req.abort(
                                500,
                                f"render() in {page_file} returned None. Check your logic.",
                            )
                        return resolve_if_coro(res)

                    if hasattr(module, "CONTENT"):
                        return module.CONTENT

                    if supported and req.method not in supported:
                        req.method_not_allowed(supported)
                else:
                    content_raw = self._read_template(page_file)
                    tpl_ctx = {
                        "request": req,
                        "__": req.__,
                        "static": req.static,
                        "get_flashed_messages": req.get_flashed_messages,
                    }
                    return render_template_string(
                        content_raw, tpl_ctx, root_dir=tpl_root
                    )

                req.status = "404 Not Found"
                return "<h1>404 Not Found</h1><p>The requested route does not provide a valid handler.</p>"

            import asyncio

            try:
                loop_running = asyncio.get_running_loop().is_running()
            except RuntimeError:
                loop_running = False

            if loop_running:
                chain = self._get_async_middleware_chain(core_layer)
                with request_context(request):
                    content_str = chain(request)
            else:
                chain = self._get_middleware_chain(core_layer)
                with request_context(request):
                    content_str = chain(request)

            status_code = request.status.split(" ")[0]
            is_default_error = False
            if isinstance(content_str, str) and "<h1>" in content_str:
                is_default_error = True
            elif status_code.startswith(("4", "5")) and not isinstance(
                content_str, str
            ):
                is_default_error = True

            if status_code.startswith(("4", "5")) and is_default_error:
                content_str = self._render_error_page(request, int(status_code))
        except RedirectException as redir:

            def is_true(val):
                if isinstance(val, str):
                    return val.lower() in ("true", "yes", "1", "on")
                return bool(val)

            show_toolbar = is_true(self.config.get("TOOLBAR"))
            if "TOOLBAR" not in self.config:
                show_toolbar = is_true(self.config.get("DEBUG"))

            if show_toolbar and hasattr(request, "_asok_sql_log"):
                try:
                    request.session["_asok_redir_stats"] = {
                        "path": request.path,
                        "method": request.method,
                        "args": dict(request.args),
                        "form": dict(request.form),
                        "sql_log": request._asok_sql_log,
                    }
                    sess = request._session
                    if sess is not None and hasattr(self, "_session_store"):
                        self._session_store.save(sess.sid, sess)
                except Exception:
                    pass

            headers = [("Location", redir.url)]
            headers += self._cookie_headers(request, environ)
            status_map = {
                301: "301 Moved Permanently",
                302: "302 Found",
                303: "303 See Other",
                307: "307 Temporary Redirect",
            }
            status_str = status_map.get(redir.status, f"{redir.status} Found")
            raise _FinalRedirectException(redir.url, status_str, headers)
        except AbortException as abort:
            request.status = Request._STATUS_MAP.get(
                abort.status, f"{abort.status} Unknown"
            )
            content_str = self._render_error_page(
                request, abort.status, message=abort.message
            )
        except Exception as e:
            from ..exceptions import (
                AsokException,
                SecurityError,
                TemplateError,
                ValidationError,
            )

            if isinstance(e, SecurityError):
                request.status = "403 Forbidden"
                content_str = self._render_error_page(request, 403, message=str(e))
            elif isinstance(e, ValidationError):
                request.status = "400 Bad Request"
                content_str = self._render_error_page(request, 400, message=str(e))
            elif isinstance(e, TemplateError):
                request.status = "500 Internal Server Error"
                content_str = self._render_error_page(
                    request, 500, message=f"Template Error: {e}"
                )
            elif isinstance(e, AsokException):
                request.status = "500 Internal Server Error"
                content_str = self._render_error_page(request, 500, message=str(e))
            else:
                error_id = str(uuid.uuid4())[:8]

                logger.error(
                    "[ERROR-ID:%s] Unhandled Exception: %s\n%s",
                    error_id,
                    str(e),
                    traceback.format_exc(),
                )

                # SECURITY: Never expose stack traces, even in DEBUG
                # Show user-friendly error with error ID for support
                body = self._render_error_page(
                    request,
                    500,
                    message=f"An unexpected error occurred. Error ID: {error_id}. Please contact support with this ID if the problem persists.",
                )
                body = self._inject_assets(body, request, getattr(request, "nonce", ""))
                raise _FinalResponseException(body, 500)

        return content_str

    def _finalize_response(
        self,
        request: Request,
        content_str: Any,
        environ: dict[str, Any],
        is_head: bool,
        start_response: Callable,
    ) -> list[bytes]:
        if "asok.stream_file" in environ:
            stream_path = environ["asok.stream_file"]
            headers = [("Content-Type", request.content_type)]
            headers += environ.get("asok.extra_headers", [])
            headers += self._cookie_headers(request, environ)
            headers += self._security_headers(request=request)
            start_response(request.status, headers)
            if is_head:
                return [b""]

            def _file_iter(path, chunk_size=65536):
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

            return _file_iter(stream_path)

        if "asok.binary_response" in environ:
            output = environ["asok.binary_response"]
            headers = [("Content-Type", request.content_type)]
            headers += environ.get("asok.extra_headers", [])
            headers += self._cookie_headers(request, environ)
            headers.append(("Content-Length", str(len(output))))
            start_response(request.status, headers)
            return [b""] if is_head else [output]

        if inspect.isgenerator(content_str):
            headers = [("Content-Type", request.content_type)]
            headers += self._cookie_headers(request, environ)
            headers += self._security_headers(
                request=request, nonce=getattr(request, "nonce", None)
            )

            use_gzip = (
                self.config.get("GZIP", False)
                and "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "").lower()
            )
            if use_gzip:
                headers.append(("Content-Encoding", "gzip"))
            start_response(request.status, headers)
            return SmartStreamer(content_str, request, self)

        if "text/html" in request.content_type:
            content_str = self._inject_assets(
                content_str, request, getattr(request, "nonce", None)
            )

            should_minify = self.config.get("HTML_MINIFY")
            if should_minify is None:
                should_minify = not self.config.get("DEBUG")

            if should_minify:
                content_str = minify_html(str(content_str))

        output = str(content_str).encode("utf-8")

        headers = [("Content-Type", request.content_type)]

        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(
            request=request, nonce=getattr(request, "nonce", None)
        )
        headers += environ.get("asok.extra_headers", [])
        headers += request.response_headers

        headers.append(("X-CSRF-Token", request.csrf_token_value))
        headers.append(("Access-Control-Expose-Headers", "X-CSRF-Token"))

        cors_origins = self.config.get("CORS_ORIGINS")
        if cors_origins:
            origin = environ.get("HTTP_ORIGIN", "")
            if cors_origins == "*" or self._cors_allowed(origin):
                if origin:
                    headers.append(("Access-Control-Allow-Origin", origin))
                    headers.append(("Access-Control-Allow-Credentials", "true"))
                else:
                    headers.append(("Access-Control-Allow-Origin", "*"))
                headers.append(
                    (
                        "Access-Control-Allow-Methods",
                        "GET, POST, PUT, DELETE, PATCH, OPTIONS",
                    )
                )
                headers.append(
                    (
                        "Access-Control-Allow-Headers",
                        "Content-Type, X-CSRF-Token, X-Block",
                    )
                )

        if (
            self.config.get("GZIP", False)
            and len(output) > self.config.get("GZIP_MIN_SIZE", 500)
            and "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "").lower()
        ):
            import gzip as gzip_mod
            import io

            buf = io.BytesIO()
            with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
                f.write(output)
            output = buf.getvalue()
            headers.append(("Content-Encoding", "gzip"))
            headers.append(("Vary", "Accept-Encoding"))

        if hasattr(request, "_asok_blocks") and request._asok_blocks:
            blocks_str = ",".join(request._asok_blocks)
            headers.append(("X-Asok-Blocks", blocks_str))

            show_toolbar = self.config.get("DEBUG") or self.config.get("TOOLBAR")
            if show_toolbar and hasattr(request, "_asok_sql_log"):
                sql_log = request._asok_sql_log
                headers.append(("X-Asok-SQL-Count", str(len(sql_log))))
                try:
                    headers.append(("X-Asok-SQL-Log", json.dumps(sql_log)))
                except Exception:
                    pass
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

            exposed = [h[1] for h in headers if h[0] == "Access-Control-Expose-Headers"]
            if exposed:
                headers = [
                    h for h in headers if h[0] != "Access-Control-Expose-Headers"
                ]
                headers.append(
                    (
                        "Access-Control-Expose-Headers",
                        f"{exposed[0]}, X-Asok-Blocks",
                    )
                )
            else:
                headers.append(("Access-Control-Expose-Headers", "X-Asok-Blocks"))

        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [b""] if is_head else [output]

    def _wsgi_call(
        self, environ: dict[str, Any], start_response: Callable
    ) -> list[bytes]:
        """Main WSGI entry point for the Asok framework."""
        environ["asok.root"] = self.root_dir
        environ["asok.app"] = self
        environ["asok.secret_key"] = self.config.get("SECRET_KEY")

        path = environ.get("PATH_INFO", "")
        is_static = any(
            path.startswith(prefix)
            for prefix in ["/css/", "/js/", "/images/", "/uploads/"]
        )
        is_noise = path in ("/__reload", "/favicon.ico") or path.startswith(
            "/.well-known/"
        )

        if not (is_static or is_noise):
            logger.info(
                "[%s] %s (DEBUG=%s)",
                environ.get("REQUEST_METHOD"),
                path,
                self.config.get("DEBUG"),
            )

        request = Request(environ)

        token = request_var.set(request)
        try:
            # SECURITY: Generate cryptographically secure nonce for CSP
            # token_urlsafe(16) = 16 bytes = 128 bits of entropy
            # This meets CSP Level 3 recommendations (minimum 128 bits)
            # The nonce is base64url-encoded, resulting in ~22 characters
            self.nonce = secrets.token_urlsafe(16)
            request._nonce = self.nonce

            _ = request.session

            is_head = request.method == "HEAD"
            if is_head:
                request.method = "GET"

            if getattr(request, "_body_rejected", False):
                start_response(
                    "413 Payload Too Large", [("Content-Type", "text/plain")]
                )
                return [b"Request body too large"]

            # OPTIONS Request handler
            res = self._handle_options_request(request, environ, start_response)
            if res is not None:
                return res

            # Health Check
            if request.path == "/__health":
                body = b'{"status":"ok"}'
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                    ],
                )
                return [body]

            # Live Reload Polling
            res = self._handle_reload_request(request, start_response)
            if res is not None:
                return res

            # Admin panel handler
            res = self._handle_admin_request(request, environ, start_response)
            if res is not None:
                return res

            # API Docs handler
            res = self._handle_docs_request(request, start_response)
            if res is not None:
                return res

            # Static Files handler
            res = self._handle_static_request(request, environ, start_response)
            if res is not None:
                return res

            # Dispatch Page Controller / Template
            try:
                result = self._dispatch_controller(request, environ)
            except _FinalResponseException as fre:
                status_str = Request._STATUS_MAP.get(
                    fre.status_code, f"{fre.status_code} Unknown"
                )
                start_response(
                    status_str,
                    [("Content-Type", f"{fre.content_type}; charset=utf-8")],
                )
                return [
                    fre.body.encode("utf-8") if isinstance(fre.body, str) else fre.body
                ]
            except _FinalRedirectException as frde:
                start_response(frde.status_str, frde.headers)
                return [b""]
            except Exception as e:
                # Catch any unhandled exceptions and return 500
                error_id = str(uuid.uuid4())[:8]
                logger.error(
                    "[ERROR-ID:%s] Unhandled Exception in WSGI: %s\n%s",
                    error_id,
                    str(e),
                    traceback.format_exc(),
                )

                # SECURITY: Never expose stack traces to client
                # All error details are logged above for debugging
                if True:  # Always use error page, even in DEBUG
                    error_page = self._render_error_page(
                        request, 500, message="An internal error occurred."
                    )
                    start_response(
                        "500 Internal Server Error",
                        [("Content-Type", "text/html; charset=utf-8")],
                    )
                    return [
                        error_page.encode("utf-8")
                        if isinstance(error_page, str)
                        else error_page
                    ]

            # Finalize Response
            return self._finalize_response(
                request, result, environ, is_head, start_response
            )
        finally:
            request_var.reset(token)
