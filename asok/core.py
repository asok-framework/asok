from __future__ import annotations

import gzip as gzip_mod
import hashlib
import hmac
import html as _html
import importlib.util
import inspect
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import traceback
from typing import Any, Callable, Iterator, Optional, Union
from urllib.parse import quote, urlparse

from .exceptions import AbortException, RedirectException
from .orm import Model
from .request import Request
from .session import SessionStore
from .templates import render_template_string
from .utils.css import scope_css
from .utils.js import scope_js
from .utils.minify import minify_css, minify_html, minify_js

logger = logging.getLogger("asok.security")


class SmartStreamer:
    """
    Advanced streaming response wrapper that handles:
    1. Automatic asset injection (JS/CSS)
    2. HTML minification on the fly
    3. Proper buffer management to avoid cutting tags
    4. SPA block extraction
    """

    def __init__(self, generator: Iterator[str], request: Request, app: "Asok"):
        self.generator = generator
        self.request = request
        self.app = app
        self.nonce = getattr(request, "nonce", secrets.token_urlsafe(16))
        self.buffer_str = ""

    def __iter__(self) -> Iterator[bytes]:
        write = self.request.environ.get("asok.write")
        if not write:

            def write_fallback(data: bytes):
                return data

            write = write_fallback

        # In production, files are already minified by 'asok build'.
        # We only minify at runtime if explicitly enabled or if in DEBUG mode.
        should_minify = self.app.config.get("HTML_MINIFY")
        if should_minify is None:
            should_minify = self.app.config.get("DEBUG", True)

        def finalize(text):
            if not should_minify or not text:
                return text
            return minify_html(text)

        try:
            # Buffer EVERYTHING to avoid chunking issues with asset injection
            full_content = ""
            for chunk_str in self.generator:
                full_content += chunk_str

            # Now inject all assets in one go on the complete document
            final_content = self.app._inject_assets(
                full_content, self.request, self.nonce, stream=False, only_scripts=False
            )

            yield write(finalize(final_content).encode("utf-8"))

        except Exception as e:
            logger.error(f"Streamer Error: {e}\n{traceback.format_exc()}")
            if self.app.config.get("DEBUG"):
                yield write(f"\n<!-- STREAM ERROR: {e} -->\n".encode("utf-8"))

        finally:
            # Save session if modified during template rendering
            if self.request._session is not None and self.request._session.modified:
                self.app._session_store.save(
                    self.request._session.sid, self.request._session
                )


class Asok:
    """The central application class for the Asok framework.

    Manages configuration, routing, middleware, and request lifecycle.
    """

    def __init__(self, root_dir: Optional[str] = None):
        """Initialize the application.

        Args:
            root_dir: The root directory of the project. Defaults to current working directory.
        """
        self.root_dir: str = os.path.abspath(root_dir or os.getcwd())
        import asok

        self.version = getattr(asok, "__version__", "0.1.0")
        self.dirs: dict[str, str] = {
            "LOCALES": "src/locales",
            "PAGES": "src/pages",
            "MIDDLEWARES": "src/middlewares",
            "MODELS": "src/models",
            "PARTIALS": "src/partials",
            "COMPONENTS": "src/components",
        }
        self.middleware_handlers: list[Callable] = []
        self.models: list[type[Model]] = []
        self.locales: dict[str, dict[str, str]] = {}
        self._shared: dict[str, Any] = {}
        self.config: dict[str, Any] = {
            "INDEX": "page",
            "LOCALE": "en",
            "CSRF": True,
            "DEBUG": True,
            "AUTH_MODEL": "User",
            "SESSION_MAX_AGE": 86400 * 30,
            "CORS_ORIGINS": None,
            "GZIP": False,
            "GZIP_MIN_SIZE": 500,
            "SECURITY_HEADERS": True,
            "SESSION_BACKEND": "memory",
            "SESSION_PATH": ".asok/sessions",
            "SESSION_TTL": 86400,
            "MAX_CONTENT_LENGTH": 10 * 1024 * 1024,  # 10 MB
            "HTML_MINIFY": None,  # Follows !DEBUG if None
            "TRUSTED_PROXIES": None,  # Set to list of IPs or "*" to trust X-Forwarded-For
        }

        # Lifecycle hooks
        self._on_startup: list[Callable] = []
        self._on_shutdown: list[Callable] = []

        # Caches (populated in production, bypassed in DEBUG)
        self._route_cache: dict[str, tuple[str, dict[str, str]]] = {}
        self._module_cache: dict[str, Any] = {}
        self._static_cache: dict[str, tuple[bytes, str]] = {}
        self._static_cache_size: int = 0
        self._static_cache_max: int = 50 * 1024 * 1024  # 50 MB max
        self._template_cache: dict[str, str] = {}
        self._middleware_chain: Optional[Callable] = None

        self.setup()

    def setup(self) -> None:
        """Configure the application environment, load models, and prepare internal states."""

        src_path = os.path.join(self.root_dir, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # 1. Load .env if exists to populate os.environ early
        env_path = os.path.join(self.root_dir, ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k.strip()] = v.strip()

        # 2. Determine DEBUG mode early
        debug_env = os.environ.get("DEBUG", "").lower()
        if debug_env == "false":
            self.config["DEBUG"] = False
        elif debug_env == "true":
            self.config["DEBUG"] = True

        # 2.5 Determine DOCS mode
        docs_val = os.environ.get("ASOK_DOCS", os.environ.get("DOCS", "")).lower()
        if docs_val == "false":
            self.config["DOCS"] = False
        elif docs_val == "true":
            self.config["DOCS"] = True
        else:
            # Default to DEBUG mode if not explicitly set
            self.config["DOCS"] = self.config.get("DEBUG", True)

        # 3. Security Key (respects DEBUG mode determined above)
        sec_key = os.getenv("SECRET_KEY")
        if not sec_key:
            if self.config.get("DEBUG"):
                # Stable dev key based on project path to survive hot-reloads
                h = hashlib.md5(self.root_dir.encode()).hexdigest()
                sec_key = f"dev-secret-{h}"
                logger.warning(
                    "Running with auto-generated SECRET_KEY (DEBUG mode). "
                    "Set SECRET_KEY in your .env before deploying to production."
                )
            else:
                raise RuntimeError(
                    "SECRET_KEY environment variable is required in production. "
                    "Set it in your .env file or environment: SECRET_KEY=your-secret-key"
                )

        self.config["SECRET_KEY"] = sec_key
        os.environ["SECRET_KEY"] = sec_key
        self.config.setdefault("WS_PORT", 8001)

        # Load Middlewares
        mw_dir = os.path.join(self.root_dir, self.dirs["MIDDLEWARES"])
        if os.path.exists(mw_dir):
            for filename in sorted(os.listdir(mw_dir)):
                if filename.endswith(".py") and not filename.startswith("__"):
                    filepath = os.path.join(mw_dir, filename)
                    spec = importlib.util.spec_from_file_location(
                        f"mw_{filename}", filepath
                    )
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    if hasattr(mod, "handle"):
                        self.middleware_handlers.append(mod.handle)

        # Load Models
        model_dir = os.path.join(self.root_dir, self.dirs["MODELS"])
        if os.path.exists(model_dir):
            for filename in sorted(os.listdir(model_dir)):
                if filename.endswith(".py") and not filename.startswith("__"):
                    filepath = os.path.join(model_dir, filename)
                    spec = importlib.util.spec_from_file_location(
                        f"model_{filename}", filepath
                    )
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, Model)
                            and attr is not Model
                        ):
                            attr.create_table()
                            self.models.append(attr)

        # Load Components
        comp_dir = os.path.join(self.root_dir, self.dirs["COMPONENTS"])
        if os.path.exists(comp_dir):
            import sys as _sys

            for filename in sorted(os.listdir(comp_dir)):
                if filename.endswith(".py") and not filename.startswith("__"):
                    filepath = os.path.join(comp_dir, filename)
                    # Use name WITHOUT .py so inspect.getfile() can resolve the path
                    mod_name = f"comp_{filename[:-3]}"
                    spec = importlib.util.spec_from_file_location(mod_name, filepath)
                    mod = importlib.util.module_from_spec(spec)
                    # Register before exec so inspect.getmodule() finds it
                    _sys.modules[mod_name] = mod
                    spec.loader.exec_module(mod)

        # Load Locales
        locale_dir = os.path.join(self.root_dir, self.dirs["LOCALES"])
        if os.path.exists(locale_dir):
            for filename in os.listdir(locale_dir):
                if filename.endswith(".json"):
                    lang = filename[:-5]
                    with open(
                        os.path.join(locale_dir, filename), "r", encoding="utf-8"
                    ) as f:
                        self.locales[lang] = json.load(f)

        # Pre-build static set for fast lookup
        self._static_dirs = frozenset(["images", "css", "js", "uploads"])

        # Pre-compute partials root (used many times)
        self._partials_path = os.path.join(self.root_dir, self.dirs["PARTIALS"])
        self._tpl_root = os.path.abspath(os.path.join(self.root_dir, "src/partials"))

        # Static versioning cache (production only)
        self._static_hashes = {}

        # Session store
        self._session_store = SessionStore(
            backend=self.config["SESSION_BACKEND"],
            path=os.path.join(self.root_dir, self.config["SESSION_PATH"]),
            ttl=self.config["SESSION_TTL"],
        )
        # Auto-cleanup expired sessions every hour
        self._session_store.start_cleanup_timer(interval=3600)

    def _sign(self, value: Union[str, int]) -> str:
        """Sign a value using the application's secret key."""
        key = self.config.get("SECRET_KEY", "").encode()
        if not key:
            raise RuntimeError("SECRET_KEY is not configured")
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature and return the original value if successful."""
        if not signed_value or "." not in signed_value:
            return None
        try:
            val, _ = signed_value.rsplit(".", 1)
            if hmac.compare_digest(self._sign(val), signed_value):
                return val
        except Exception:
            pass
        return None

    def use(self, middleware: Callable, priority: int = 50) -> Asok:
        """Register a middleware handler programmatically with optional priority.

        Lower priority values run first (outermost in chain).

        Args:
            middleware: A callable with signature (request, next_handler) -> response.
            priority: Ordering value (default 50). Lower = runs earlier.

        Returns:
            The Asok instance for method chaining.
        """
        self.middleware_handlers.append(middleware)
        if not hasattr(middleware, "_asok_priority"):
            middleware._asok_priority = priority
        # Re-sort by priority
        self.middleware_handlers.sort(key=lambda m: getattr(m, "_asok_priority", 50))
        # Invalidate cached chain
        self._middleware_chain = None
        return self

    def on_startup(self, fn: Callable) -> Callable:
        """Register a function to be called when the app starts up.

        Can be used as a decorator:
            @app.on_startup
            def setup():
                ...
        """
        self._on_startup.append(fn)
        return fn

    def on_shutdown(self, fn: Callable) -> Callable:
        """Register a function to be called when the app shuts down.

        Can be used as a decorator:
            @app.on_shutdown
            def cleanup():
                ...
        """
        self._on_shutdown.append(fn)
        return fn

    def startup(self) -> None:
        """Run all registered startup hooks."""
        for fn in self._on_startup:
            fn()

    def shutdown(self) -> None:
        """Run all registered shutdown hooks."""
        for fn in self._on_shutdown:
            fn()
        if hasattr(self, "_session_store"):
            self._session_store.stop_cleanup_timer()

    def share(self, **kwargs: Any) -> Asok:
        """Register variables to be shared across all template contexts.

        Shared variables are accessible in templates directly as variables.
        Common use cases include the site name, global forms, or current user.

        Args:
            **kwargs: Key-value pairs of variables to share. Values can be static,
                      callables(request), or Form templates.

        Returns:
            The Asok instance for method chaining.
        """
        self._shared.update(kwargs)
        return self

    def _static_hash(self, filepath: str) -> Optional[str]:
        """Compute and cache an MD5 hash of a static file for versioning."""
        if filepath in self._static_hashes:
            return self._static_hashes[filepath]
        full_path = os.path.join(self._partials_path, filepath.lstrip("/"))
        if not os.path.isfile(full_path):
            return None
        with open(full_path, "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()[:8]
        self._static_hashes[filepath] = h
        return h

    # ── Live reload (DEBUG only) ────────────────────────────

    _WATCH_IGNORE_DIRS = ("uploads", "__pycache__", ".git")
    _mtime_cache: float = 0
    _mtime_cache_ts: float = 0
    _MTIME_CACHE_TTL: float = 0.5  # seconds — avoid re-scanning more than 2x/sec

    def _get_src_mtime(self):
        import time as _time

        now = _time.monotonic()
        if now - self._mtime_cache_ts < self._MTIME_CACHE_TTL:
            return self._mtime_cache

        max_mtime = 0
        src_dir = os.path.join(self.root_dir, "src")
        if not os.path.isdir(src_dir):
            return 0
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

    # ── Routing ──────────────────────────────────────────────

    def _resolve_route(self, parts: list[str]) -> tuple[Optional[str], dict[str, str]]:
        """Resolve a list of URL segments to a page file and captured parameters."""
        # Disable routing cache for now to prevent corruption in production
        # if not debug and cache_key in self._route_cache:
        #    match, params = self._route_cache[cache_key]
        #    return match, params.copy()

        current_dir = os.path.join(self.root_dir, self.dirs["PAGES"])
        result = self._walk_route(parts, current_dir, {})

        # if not self.config.get("DEBUG"):
        #     self._route_cache[cache_key] = result

        return result

    def _convert_param(self, value: str, type_name: str) -> Optional[Any]:
        """Convert a URL segment to a typed parameter (int, float, uuid, slug)."""
        if type_name == "int":
            try:
                return int(value)
            except ValueError:
                if self.config.get("DEBUG"):
                    logger.debug(
                        f"Routing: Int validation failed for segment '{value}'"
                    )
                return None
        if type_name == "float":
            try:
                return float(value)
            except ValueError:
                if self.config.get("DEBUG"):
                    logger.debug(
                        f"Routing: Float validation failed for segment '{value}'"
                    )
                return None
        if type_name == "uuid":
            # Support standard (8-4-4-4-12) and compact (32 hex) formats, case-insensitive
            # Optional {} for full standard formats
            pattern = r"^({)?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}(?(1)})$"
            if re.match(pattern, value, re.I):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: UUID validation failed for segment '{value}'")
            return None
        if type_name == "hex":
            # Support hex characters and optional hyphens (1-64 chars)
            if re.match(r"^[0-9a-f-]{1,64}$", value, re.I):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Hex validation failed for segment '{value}'")
            return None
        if type_name == "slug":
            if re.match(r"^[a-z0-9-]+$", value):
                return value

            if self.config.get("DEBUG"):
                logger.debug(f"Routing: Slug validation failed for segment '{value}'")
            return None
        return value  # str or unknown type

    def _walk_route(
        self, segments: list[str], current_base: str, captured_params: dict[str, Any]
    ) -> tuple[Optional[str], dict[str, Any]]:
        """Recursively walk the pages directory to find a matching route file."""
        if not segments:
            for ext in (".py", ".html"):
                p = os.path.join(current_base, self.config["INDEX"] + ext)
                if os.path.isfile(p):
                    return p, captured_params
            return None, captured_params

        seg = segments[0]
        remaining = segments[1:]

        # 1. Literal Directory match
        dir_candidate = os.path.join(current_base, seg)
        if os.path.isdir(dir_candidate):
            res, pars = self._walk_route(remaining, dir_candidate, captured_params)
            if res:
                return res, pars

        # 2. Dynamic Directory match [param] or [param:type]
        try:
            entries = os.listdir(current_base)
        except OSError:
            return None, captured_params

        candidates = []
        for entry in entries:
            if (
                entry[0] == "["
                and entry[-1] == "]"
                and os.path.isdir(os.path.join(current_base, entry))
            ):
                candidates.append(entry)

        # Priority: Typed matches ([id:int]) before Generic matches ([name])
        typed = sorted([c for c in candidates if ":" in c])
        generic = [c for c in candidates if ":" not in c]

        for entry in typed + generic:
            inner = entry[1:-1]
            if ":" in inner:
                param_name, type_name = inner.split(":", 1)
                converted = self._convert_param(seg, type_name)
                if converted is None:
                    if self.config.get("DEBUG"):
                        logger.debug(
                            f"Routing: Folder '{entry}' rejected segment '{seg}' due to type mismatch ({type_name})"
                        )
                    continue  # Type mismatch, try next folder
            else:
                param_name = inner
                converted = seg

            new_params = captured_params.copy()
            new_params[param_name] = converted
            res, pars = self._walk_route(
                remaining, os.path.join(current_base, entry), new_params
            )
            if res:
                return res, pars

        return None, captured_params

    # ── Module loading ───────────────────────────────────────

    def _load_module(self, page_file: str) -> Any:
        """Dynamically load a Python module from a page file."""
        debug = self.config.get("DEBUG")

        if not debug and page_file in self._module_cache:
            return self._module_cache[page_file]

        spec = importlib.util.spec_from_file_location(
            f"page_{id(page_file)}", page_file
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not debug:
            self._module_cache[page_file] = module

        return module

    # ── Error pages ──────────────────────────────────────────

    def _render_error_page(
        self, request: Request, code: int, message: Optional[str] = None
    ) -> str:
        """Render a custom error page or return a fallback heading."""
        for ext in (".html", ".py"):
            error_file = os.path.join(
                self.root_dir, self.dirs["PAGES"], str(code), self.config["INDEX"] + ext
            )
            if os.path.isfile(error_file):
                try:
                    # Inject global shared variables into the error context
                    shared_vars = self._shared

                    # Set context for relative template lookups
                    request._current_page_file = error_file
                    request.environ["asok.page_dir"] = os.path.dirname(error_file)

                    # Set page_id for scoped assets (e.g. error-404)
                    request.page_id = f"error-{code}"

                    # Populate request.meta for automatic <title> and <meta> override
                    request.meta.title(f"Error {code}")
                    request.meta.description(message or "An error occurred.")

                    # Direct injection into params to be 100% sure templates see it
                    request.params["title"] = f"Error {code}"
                    request.params["description"] = message or "An error occurred."

                    # Detect scoped assets for error pages (Works for both .py and .html)
                    base_path = error_file.rsplit(".", 1)[0]
                    for a_ext in ("css", "js"):
                        p = f"{base_path}.{a_ext}"
                        if os.path.isfile(p):
                            request.scoped_assets[a_ext] = p

                    if ext == ".py":
                        mod = self._load_module(error_file)
                        if hasattr(mod, "render"):
                            # Update request params to include shared vars for the template
                            for k, v in shared_vars.items():
                                if k not in request.params:
                                    request.params[k] = v
                            return mod.render(request)
                    else:
                        content = self._read_template(error_file)
                        ctx = {
                            "request": request,
                            "__": request.__,
                            "static": request.static,
                            "get_flashed_messages": request.get_flashed_messages,
                            "error_message": message,
                            "title": f"Error {code}",
                            "description": "An error occurred.",
                            "nonce": getattr(request, "nonce", ""),
                            **shared_vars,
                        }
                        return render_template_string(
                            content, ctx, root_dir=self._tpl_root
                        )
                except Exception as e:
                    import traceback

                    logger.error(
                        f"Error rendering custom {code} page: {e}\n{traceback.format_exc()}"
                    )
                    pass

        fallback = f"<h1>{code}</h1>"
        if message:
            fallback += f"<p>{message}</p>"
        return fallback

    # ── Template reading with cache ──────────────────────────

    def _read_template(self, path: str) -> str:
        """Read a template file from disk, using a cache in production."""
        debug = self.config.get("DEBUG")

        if not debug and path in self._template_cache:
            return self._template_cache[path]

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if not debug:
            self._template_cache[path] = content

        return content

    # ── Static file serving with cache ───────────────────────

    def _serve_static(
        self,
        static_path: str,
        start_response: Callable,
        environ: Optional[dict[str, Any]] = None,
    ) -> Optional[list[bytes]]:
        """Serve a static file with appropriate mime types and caching headers."""
        debug = self.config.get("DEBUG")

        if not debug and static_path in self._static_cache:
            content, mimetype, etag = self._static_cache[static_path]
        else:
            if not os.path.isfile(static_path):
                return None
            mimetype, _ = mimetypes.guess_type(static_path)
            mimetype = mimetype or "application/octet-stream"
            with open(static_path, "rb") as f:
                content = f.read()
            etag = hashlib.md5(content).hexdigest()
            if (
                not debug
                and self._static_cache_size + len(content) <= self._static_cache_max
            ):
                self._static_cache[static_path] = (content, mimetype, etag)
                self._static_cache_size += len(content)

        # ETag / 304 Not Modified
        if environ and not debug:
            if_none_match = environ.get("HTTP_IF_NONE_MATCH", "").strip()
            if if_none_match and if_none_match == etag:
                start_response(
                    "304 Not Modified",
                    [
                        ("ETag", etag),
                        ("Cache-Control", "public, max-age=86400"),
                    ],
                )
                return [b""]

        headers = [
            ("Content-Type", mimetype),
            ("Content-Length", str(len(content))),
            (
                "Cache-Control",
                "public, max-age=86400" if not debug else "no-cache, no-store",
            ),
        ]
        if not debug:
            headers.append(("ETag", etag))

        start_response("200 OK", headers)
        return [content]

    # ── Middleware chain (pre-built) ─────────────────────────

    def _get_middleware_chain(self, core_layer: Callable) -> Callable:
        """Compose the user middleware handlers into a single callable chain."""
        if not self.middleware_handlers:
            return core_layer

        # We must NOT cache the chain because core_layer is dynamic per request
        chain = core_layer
        for mw_handle in reversed(self.middleware_handlers):

            def mw_wrapper(req, mw=mw_handle, nxt=chain):
                return mw(req, nxt)

            chain = mw_wrapper
        return chain

    # ── Cookie headers ───────────────────────────────────────

    def _cookie_headers(
        self, request: Request, environ: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Determine all cookie headers (Set-Cookie) to be sent with the response."""
        headers = []
        if "asok.session_cookie" in environ:
            headers.append(("Set-Cookie", environ["asok.session_cookie"]))
        # Always send session cookie to ensure it persists across requests
        # This is especially important for streaming responses where the session
        # may be modified after headers are sent
        if request._session is not None:
            sid = request._session.sid
            # Save if modified (will be saved again in SmartStreamer if modified during streaming)
            if request._session.modified:
                self._session_store.save(sid, request._session)
            # Always send the cookie (not just when modified)
            signed = self._sign(sid)
            debug = self.config.get("DEBUG")
            cookie = f"asok_sid={signed}; HttpOnly; Path=/; SameSite=Lax; Max-Age={self.config['SESSION_TTL']}"
            if not debug:
                cookie += "; Secure"
            headers.append(("Set-Cookie", cookie))
        csrf_cookie = f"{request._csrf_cookie_name}={request.csrf_token_value}; Path=/; HttpOnly; SameSite=Lax"
        if not self.config.get("DEBUG"):
            csrf_cookie += "; Secure"
        headers.append(("Set-Cookie", csrf_cookie))
        if request._new_flashes and not request._new_flashes_consumed:
            # Redirect case: persist new flashes for next request (HMAC-signed)
            signed_flash = self._sign(json.dumps(request._new_flashes))
            headers.append(
                (
                    "Set-Cookie",
                    f"{request._flash_cookie_name}={quote(signed_flash)}; Path=/; HttpOnly; SameSite=Lax",
                )
            )
        elif request.flashed_messages or request._new_flashes_consumed:
            # Old flashes displayed, or new flashes consumed inline → clear cookie
            headers.append(
                ("Set-Cookie", f"{request._flash_cookie_name}=; Path=/; Max-Age=0")
            )
        if request.args.get("lang"):
            lang_cookie = f"asok_lang={request.args['lang']}; Path=/; SameSite=Lax; Max-Age=31536000"
            if not self.config.get("DEBUG"):
                lang_cookie += "; Secure"
            headers.append(("Set-Cookie", lang_cookie))
        return headers

    def _inject_assets(
        self,
        content: str,
        request: Request,
        nonce: str,
        stream: bool = False,
        include_scripts: bool = True,
        only_scripts: bool = False,
    ) -> str:
        """Inject required CSRF tags, metadata, and scripts into the HTML response."""
        if not isinstance(content, str):
            return content

        if not hasattr(request, "_asok_pending_scripts"):
            request._asok_pending_scripts = ""
        if not hasattr(request, "_asok_pending_styles"):
            request._asok_pending_styles = ""

        # 0. SEO Metadata (Title, Metas, Links)
        meta_html = ""
        # Only inject metadata once
        if not only_scripts and not getattr(request, "_asok_meta_done", False):
            meta_obj = getattr(request, "meta", None)
            if meta_obj:
                # 0.1 Handle Title (Replacement-aware)
                if meta_obj._title:
                    # Robust removal of ANY existing title tag
                    if "<title>" in content.lower():
                        start = content.lower().find("<title>")
                        end = content.lower().find("</title>", start)
                        if end != -1:
                            content = content[:start] + content[end + 8 :]

                    meta_html += (
                        f"    <title>{_html.escape(str(meta_obj._title))}</title>\n"
                    )

            # 0.2 Handle Description (Replacement-aware)
            if meta_obj._description:
                # Remove any existing description tags to ensure override
                content = re.sub(
                    r'<meta\s+name=["\']description["\']\s+content=["\'].*?["\']\s*/?>',
                    "",
                    content,
                    flags=re.IGNORECASE,
                )
                meta_html += f'    <meta name="description" content="{_html.escape(str(meta_obj._description))}">'
                meta_html += "\n"

            for item in meta_obj._items:
                itype, ikey, ival, ikwargs = item
                if itype == "name":
                    # Special case for description if handled above
                    if ikey.lower() == "description" and meta_obj._description:
                        continue
                    meta_html += f'    <meta name="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">'
                elif itype == "property":
                    meta_html += f'    <meta property="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">'
                elif itype == "link":
                    extra = " ".join(
                        f'{k}="{_html.escape(str(v))}"' for k, v in ikwargs.items()
                    )
                    meta_html += f'    <link rel="{_html.escape(ikey)}" href="{_html.escape(ival)}" {extra}>'
                meta_html += "\n"

            if meta_html:
                request._asok_meta_done = True

        if meta_html:
            if "<head>" in content:
                content = content.replace("<head>", "<head>\n" + meta_html, 1)
            elif "<head " in content:
                idx = content.find("<head ")
                end = content.find(">", idx)
                if end != -1:
                    content = content[: end + 1] + "\n" + meta_html + content[end + 1 :]

        # 0.5 Scoped Assets (CSS/JS) and Page ID
        page_id = getattr(request, "page_id", "unknown")
        if request.page_id:
            # 1. Inject Page ID Marker (for SPA engine)
            if not getattr(request, "_asok_page_id_done", False):
                # Tag body for CSS scoping
                if "<body" in content:
                    if 'data-page-id="' not in content:
                        content = content.replace(
                            "<body", f'<body data-page-id="{page_id}"', 1
                        )
                    else:
                        content = re.sub(
                            r'data-page-id="[^"]*"',
                            f'data-page-id="{page_id}"',
                            content,
                            1,
                        )

                marker = f'\n<div id="asok-page-id-marker" data-page-id="{page_id}" style="display:none"></div>\n'
                if "</body>" in content.lower():

                    def inject_marker(m):
                        return marker + m.group(1)

                    content = re.sub(
                        r"(</body>)", inject_marker, content, flags=re.I, count=1
                    )
                    request._asok_page_id_done = True
                elif not stream:
                    content += marker
                    request._asok_page_id_done = True

            # 2. Inject Scoped CSS
            if not getattr(request, "_asok_css_done", False):
                if request.scoped_assets.get("css"):
                    try:
                        with open(
                            request.scoped_assets["css"], "r", encoding="utf-8"
                        ) as f:
                            raw_css = f.read()

                        scoped_css_content = scope_css(raw_css, page_id)
                        if not self.config.get("DEBUG"):
                            scoped_css_content = minify_css(scoped_css_content)
                        style_tag = f'\n<style id="asok-scoped-css" data-page-id="{page_id}">\n{scoped_css_content}\n</style>\n'

                        if "</head>" in content.lower():

                            def inject_css(m):
                                return style_tag + m.group(1)

                            content = re.sub(
                                r"(</head>)", inject_css, content, flags=re.I, count=1
                            )
                            request._asok_css_done = True
                        else:
                            # For streamed chunks, fragments, or SPA blocks, just prepend
                            content = style_tag + content
                            request._asok_css_done = True
                    except Exception:
                        pass

            # 3. Inject Scoped JS
            if not getattr(request, "_asok_js_done", False):
                if request.scoped_assets.get("js"):
                    try:
                        with open(
                            request.scoped_assets["js"], "r", encoding="utf-8"
                        ) as f:
                            raw_js = f.read()
                        scoped_js_content = scope_js(raw_js)
                        if not self.config.get("DEBUG"):
                            scoped_js_content = minify_js(scoped_js_content)
                        request._asok_pending_scripts += (
                            f'\n<script id="asok-scoped-js" nonce="{nonce}">'
                            "(function(){"
                            "const init=function(){" + scoped_js_content + "};"
                            "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);"
                            "else init();"
                            "})()"
                            "</script>\n"
                        )
                        request._asok_js_done = True
                    except Exception:
                        pass

        # 4. Final Injection of accumulated styles
        styles = request._asok_pending_styles
        if styles and not getattr(request, "_asok_styles_done", False):
            if "</head>" in content.lower():
                request._asok_styles_done = True
                request._asok_pending_styles = ""  # Clear

                def inject_styles(m):
                    return styles + m.group(1)

                content = re.sub(
                    r"(</head>)", inject_styles, content, flags=re.I, count=1
                )
            elif not stream:
                # Fallback for fragments
                request._asok_styles_done = True
                request._asok_pending_styles = ""
                content = styles + content

        # [END OF ASSET INJECTION]
        if not only_scripts and not getattr(request, "_asok_csrf_done", False):
            csrf_meta = f'<meta name="csrf-token" content="{getattr(request, "csrf_token_value", "")}">'
            if "<head>" in content.lower():

                def inject_csrf(m):
                    return m.group(1) + "\n" + csrf_meta

                content = re.sub(
                    r"(<head.*?>)", inject_csrf, content, flags=re.I, count=1
                )
                request._asok_csrf_done = True

        # 2. Asok Transitions (Independent & Opt-in)
        # Skip engine injection if it's a block request (already on parent page)
        is_block = bool(request.environ.get("HTTP_X_BLOCK"))
        needs_transition = (
            "asok-transition" in content
            and not is_block
            and not getattr(request, "_asok_transition_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_transition_done", False)
        ):
            needs_transition = True

        if needs_transition:
            request._asok_transition_done = True
            # Shared transition styles
            request._asok_pending_scripts += (
                f'<style id="asok-transitions" nonce="{nonce}">'
                ".asok-transitioning { position: relative; overflow: hidden; pointer-events: none; }"
                ".asok-fade-out { opacity: 0; transition: opacity 300ms ease-out; }"
                ".asok-fade-in { opacity: 0; }"
                ".asok-fade-in.is-entering { opacity: 1; transition: opacity 300ms ease-out; }"
                ".asok-slide-out { transform: translateX(0); opacity: 1; transition: all 300ms ease-in; }"
                ".asok-slide-out.is-leaving { transform: translateX(-20px); opacity: 0; }"
                ".asok-slide-in { transform: translateX(20px); opacity: 0; }"
                ".asok-slide-in.is-entering { transform: translateX(0); opacity: 1; transition: all 300ms ease-out; }"
                ".asok-scale-out { transform: scale(1); opacity: 1; transition: all 250ms ease-in; }"
                ".asok-scale-out.is-leaving { transform: scale(0.95); opacity: 0; }"
                ".asok-scale-in { transform: scale(0.95); opacity: 0; }"
                ".asok-scale-in.is-entering { transform: scale(1); opacity: 1; transition: all 300ms ease-out; }"
                "</style>"
                f'<script id="asok-transition-engine" nonce="{nonce}">'
                "(function(){"
                "window.Asok=window.Asok||{};"
                "window.Asok.swap=function(t,h,mode){"
                "const raw=function(t,h,mode){"
                "mode=mode||'innerHTML';"
                "if(mode==='delete'){t.remove();return}"
                "if(mode==='none')return;"
                "if(mode==='outerHTML'){t.outerHTML=h;return}"
                "if(mode==='innerHTML'){t.innerHTML=h;return}"
                "if(mode==='replaceWith'){const r=document.createRange().createContextualFragment(h);t.replaceWith(r);return}"
                "t.insertAdjacentHTML(mode,h)"
                "};"
                "if(t.hasAttribute('asok-transition')){"
                "const tr=t.getAttribute('asok-transition')||'fade',parts=tr.split(' '),type=parts[0],dur=parseInt(parts[1])||300;"
                "t.classList.add('asok-'+type+'-out');"
                "requestAnimationFrame(()=>{t.classList.add('is-leaving')});"
                "setTimeout(()=>{raw(t,h,mode);t.classList.remove('asok-'+type+'-out','is-leaving');"
                "t.classList.add('asok-'+type+'-in');"
                "requestAnimationFrame(()=>{t.classList.add('is-entering');"
                "setTimeout(()=>{t.classList.remove('asok-'+type+'-in','is-entering')},dur)});"
                "},dur)}else{raw(t,h,mode)}"
                "};"
                "})()"
                "</script>"
            )

        needs_reactive = (
            any(
                attr in content
                for attr in ["data-block", "data-sse", "data-url", "data-method"]
            )
            and not is_block
            and not getattr(request, "_asok_reactive_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_reactive_done", False)
        ):
            needs_reactive = True

        if needs_reactive:
            request._asok_reactive_done = True
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                "(function(){const ca={};"
                "window.__asokClearCache=function(){Object.keys(ca).forEach(k=>delete ca[k])};"
                "function ct(){const m=document.querySelector('meta[name=csrf-token]');return m?m.content:''}"
                "function qb(s){if(!s)return null;let t;try{t=document.querySelector(s)}catch(e){}"
                "if(!t&&/^[a-zA-Z0-9_-]+$/.test(s))t=document.getElementById(s);return t}"
                "function doSwap(t,h,mode){"
                "  const raw=function(t,h,mode){"
                "    (window.Asok&&window.Asok.swap)?window.Asok.swap(t,h,mode):t.innerHTML=h;"
                "  };"
                "  raw(t,h,mode);"
                "  if(window.Asok&&window.Asok.init)window.Asok.init(t);"
                "  document.dispatchEvent(new CustomEvent('asok:success',{detail:{target:t,mode:mode}}));"
                "}"
                "function sw(url,b,sel,mode,opts,src){"
                "if(document.dispatchEvent(new CustomEvent('asok:before',{detail:{url:url,block:b}}))===false)return;"
                "const h=Object.assign({'X-Block':b,'X-CSRF-Token':ct()},opts.headers||{});"
                "opts.headers=h;"
                "const key=url+b;const p=ca[key]?Promise.resolve(ca[key]):fetch(url,opts).then(function(r){"
                "if(!r.ok){document.dispatchEvent(new CustomEvent('asok:error',{detail:{url:url,status:r.status}}));return r.text().then(function(t){throw t})}"
                "const redir=r.headers.get('X-Asok-Redirect');"
                "if(redir){window.location.href=redir;return Promise.reject('r')}"
                "const c=r.headers.get('X-CSRF-Token');"
                "if(c){const m=document.querySelector('meta[name=csrf-token]');if(m)m.content=c;"
                "document.querySelectorAll('input[name=csrf_token]').forEach(function(i){i.value=c})}"
                "return r.text()});"
                "delete ca[key];"
                "return p.then(function(h){"
                "if(!h)return;"
                "const tr=h.trimStart();"
                "if(tr.startsWith('<!DOCTYPE')||tr.startsWith('<html')){"
                "window.location.href=url;return}"
                "const d=document.createElement('div');d.innerHTML=h;"
                "const tpls=d.querySelectorAll('template[data-block]');"
                "if(tpls.length){"
                "for(let i=0;i<tpls.length;i++){"
                "const tpl=tpls[i];"
                "const t=qb(tpl.dataset.block);"
                "if(t)doSwap(t,tpl.innerHTML,tpl.dataset.swap||'innerHTML')}"
                "}else{"
                "const t=qb(sel);"
                "if(t)doSwap(t,h,mode)}"
                "const isP=(src&&src.dataset&&src.dataset.pushUrl!==undefined)||(!src&&url);"
                "const get=function(s){let e=d.querySelector(s);if(!e){const ts=d.querySelectorAll('template');for(let i=0;i<ts.length;i++){e=ts[i].content.querySelector(s);if(e)break}}return e};"
                "const scs=get('#asok-scoped-css');const oldCss=document.getElementById('asok-scoped-css');if(scs){if(oldCss)oldCss.remove();document.head.appendChild(scs)}else if(oldCss&&isP){oldCss.remove()}"
                "const scj=get('#asok-scoped-js');const oldJs=document.getElementById('asok-scoped-js');if(scj){if(oldJs)oldJs.remove();const ns=document.createElement('script');ns.id='asok-scoped-js';if(scj.nonce)ns.nonce=scj.nonce;ns.textContent=scj.textContent;document.body.appendChild(ns)}else if(oldJs&&isP){oldJs.remove()}"
                "const pid=get('#asok-page-id-marker');if(pid){document.body.dataset.pageId=pid.dataset.pageId}else if(isP){delete document.body.dataset.pageId}"
                "if(isP){"
                "const ov=document.getElementById('search-overlay');if(ov)ov.classList.remove('open');"
                "const mm=document.getElementById('mobile-menu');if(mm)mm.classList.add('hidden');"
                "document.body.style.overflow='';"
                "if(src&&src.dataset&&src.dataset.pushUrl!==undefined){"
                "const pu=src.dataset.pushUrl||url;"
                "history.pushState({b:b,sel:sel,mode:mode,url:url},'',pu)"
                "}"
                "}"
                "},function(){})"
                "}"
                "function pf(u,b){if(ca[u+b]||!u||!b)return;fetch(u,{headers:{'X-Block':b,'X-Prefetch':'1'}}).then(function(r){if(r.ok)r.text().then(function(t){ca[u+b]=t})})}"
                "function resolve(el){"
                "const b=el.dataset.block;if(!b)return null;"
                "const sel=el.dataset.target||b.split(',')[0];"
                "const swap=el.dataset.swap||'innerHTML';"
                "let url,method,body=null;"
                "if(el.tagName==='FORM'){"
                "url=el.action||location.pathname;"
                "method=(el.method||'GET').toUpperCase();"
                "if(method==='GET'){"
                "const p=new URLSearchParams(new FormData(el)).toString();"
                "if(p)url+=(url.indexOf('?')<0?'?':'&')+p"
                "}else body=new FormData(el)"
                "}else if(el.tagName==='A'){"
                "url=el.href;method='GET'"
                "}else{"
                "url=el.dataset.url||location.pathname;"
                "method=(el.dataset.method||'GET').toUpperCase();"
                "const f=el.closest('form');"
                "if(f){"
                "if(method==='GET'){"
                "const p=new URLSearchParams(new FormData(f)).toString();"
                "if(p)url+=(url.indexOf('?')<0?'?':'&')+p"
                "}else body=new FormData(f)"
                "}else if(el.name){"
                "if(method==='GET'){"
                "url+=(url.indexOf('?')<0?'?':'&')+encodeURIComponent(el.name)+'='+encodeURIComponent(el.value||'')"
                "}else{const fd=new FormData();fd.append(el.name,el.value||'');body=fd}"
                "}}"
                "const inc=el.dataset.include;"
                "if(inc){"
                "const extras=document.querySelectorAll(inc);"
                "extras.forEach(function(x){"
                "if(!x.name)return;"
                "if(method==='GET'){"
                "url+=(url.indexOf('?')<0?'?':'&')+encodeURIComponent(x.name)+'='+encodeURIComponent(x.value||'')"
                "}else{if(!body)body=new FormData();body.append(x.name,x.value||'')}"
                "})}"
                "return{url:url,method:method,body:body,block:b,sel:sel,swap:swap}"
                "}"
                "function indEls(el){"
                "const v=el.dataset.indicator;"
                "if(v===undefined)return[];"
                "if(v==='')return[el];"
                "return Array.prototype.slice.call(document.querySelectorAll(v))"
                "}"
                "function disEls(el){"
                "if(el.dataset.disable===undefined)return[];"
                "if(el.tagName==='FORM')return Array.prototype.slice.call(el.querySelectorAll('button,input[type=submit]'));"
                "return[el]"
                "}"
                "function fire(el){"
                "const cf=el.dataset.confirm;"
                "if(cf&&!confirm(cf))return;"
                "const r=resolve(el);if(!r)return;"
                "const opts={method:r.method};if(r.body)opts.body=r.body;"
                "const inds=indEls(el),dis=disEls(el);"
                "inds.forEach(function(x){x.classList.add('is-loading')});"
                "dis.forEach(function(x){x.disabled=true});"
                "return sw(r.url,r.block,r.sel,r.swap,opts,el).then(function(){"
                "inds.forEach(function(x){x.classList.remove('is-loading')});"
                "dis.forEach(function(x){x.disabled=false})"
                "},function(){"
                "inds.forEach(function(x){x.classList.remove('is-loading')});"
                "dis.forEach(function(x){x.disabled=false})"
                "})"
                "}"
                "function parseTrigger(s){"
                "const m=s.match(/^every\\s+(\\d+)(ms|s)$/);"
                "if(m)return{event:'every',interval:parseInt(m[1])*(m[2]==='s'?1000:1)};"
                "const parts=s.split(/\\s+/),ev=parts[0];let delay=0;"
                "for(let i=1;i<parts.length;i++){"
                "const dm=parts[i].match(/^delay:(\\d+)(ms|s)?$/);"
                "if(dm)delay=parseInt(dm[1])*(dm[2]==='s'?1000:1)}"
                "return{event:ev,delay:delay}"
                "}"
                "document.addEventListener('submit',function(e){"
                "const f=e.target;if(!f.dataset||!f.dataset.block)return;"
                "const tr=(f.dataset.trigger||'submit').split(/\\s+/)[0];"
                "if(tr!=='submit')return;"
                "e.preventDefault();fire(f)"
                "});"
                "document.addEventListener('mouseover',function(e){"
                "const a=e.target.closest('[data-block]');"
                "if(a&&a.tagName==='A'&&a.dataset.url!=='none'&&((a.dataset.trigger||'click').split(/\\s+/)[0]==='click'))pf(a.href,a.dataset.block);"
                "});"
                "document.addEventListener('click',function(e){"
                "const a=e.target.closest('[data-block]');if(!a||a.tagName==='FORM')return;"
                "const tr=(a.dataset.trigger||'click').split(/\\s+/)[0];"
                "if(tr!=='click')return;"
                "if(a.tagName==='A')e.preventDefault();"
                "fire(a)"
                "});"
                "function setup(){"
                "document.querySelectorAll('[data-sse]').forEach(function(el){"
                "if(el.__as)return;el.__as=1;"
                "const es=new EventSource(el.dataset.sse);"
                "const sel=el.dataset.block||('#'+el.id);"
                "const mode=el.dataset.swap||'innerHTML';"
                "es.onmessage=function(ev){"
                "const d=document.createElement('div');d.innerHTML=ev.data;"
                "const tpls=d.querySelectorAll('template[data-block]');"
                "if(tpls.length){"
                "for(let i=0;i<tpls.length;i++){"
                "const tpl=tpls[i];"
                "const t=qb(tpl.dataset.block);"
                "if(t)doSwap(t,tpl.innerHTML,tpl.dataset.swap||'innerHTML')}"
                "}else{"
                "const t=qb(sel);"
                "if(t)doSwap(t,ev.data,mode)}"
                "}"
                "});"
                "document.querySelectorAll('[data-block][data-trigger]').forEach(function(el){"
                "if(el.__aw)return;el.__aw=1;"
                "const t=parseTrigger(el.dataset.trigger);"
                "if(t.event==='submit'||t.event==='click')return;"
                "if(t.event==='load'){fire(el);return}"
                "if(t.event==='every'){fire(el);setInterval(function(){fire(el)},t.interval);return}"
                "let timer;"
                "el.addEventListener(t.event,function(){"
                "if(t.delay){clearTimeout(timer);timer=setTimeout(function(){fire(el)},t.delay)}"
                "else fire(el)"
                "})"
                "})"
                "}"
                "window.addEventListener('popstate',function(e){"
                "const s=e.state;if(!s||!s.b)return;"
                "sw(location.pathname+location.search,s.b,s.sel,s.mode,{method:'GET'})"
                "});"
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);"
                "else setup();"
                "})()"
                "</script>"
            )

        needs_alive = (
            ("data-asok-component" in content or "ws-" in content)
            and not is_block
            and not getattr(request, "_asok_alive_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_alive_done", False)
        ):
            needs_alive = True

        if needs_alive:
            request._asok_alive_done = True
            ws_port = self.config.get("WS_PORT", 8001)
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                "window.asokWS=function(path){"
                "const p=location.protocol==='https:'?'wss:':'ws:';"
                f"let h=location.hostname+':{ws_port}';"
                f"if(location.hostname!=='localhost'&&location.hostname!=='127.0.0.1'&&location.hostname!=='0.0.0.0'&&!location.hostname.startsWith('192.168.'))h=location.host+'/ws';"
                "return new WebSocket(p+'//'+h+path)"
                "};"
                "</script>"
            )
            # Alive Engine
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                "(function(){"
                "let ws;const timers={};function connect(){"
                "ws=window.asokWS('/asok/live');"
                "ws.onopen=function(){document.querySelectorAll('[data-asok-component]').forEach(init)};"
                "ws.onmessage=function(e){"
                "const d=JSON.parse(e.data);if(d.op==='render'){"
                "if(d.invalidate_cache&&window.__asokClearCache)window.__asokClearCache();"
                "const el=document.getElementById('asok-'+d.cid);"
                "if(el){const a=document.activeElement,aid=a?a.id:null,sel=a?a.selectionStart:null;"
                "if(window.Asok&&window.Asok.swap){window.Asok.swap(el,d.html,'outerHTML')}"
                "else{const newEl=new DOMParser().parseFromString(d.html,'text/html').body.firstElementChild;el.replaceWith(newEl);}"
                "if(aid){const r=document.getElementById(aid);if(r){r.focus();if(sel!==null)r.setSelectionRange(sel,sel)}}"
                "const updated=document.getElementById('asok-'+d.cid);if(updated)init(updated)}}};"
                "ws.onclose=function(){setTimeout(connect,1000)};"
                "}"
                "function send(msg,el){"
                "if(ws.readyState!==1)return;"
                "if(el)el.classList.add('asok-loading');"
                "ws.send(JSON.stringify(msg));"
                "}"
                "function init(el){"
                "const cid=el.id.replace('asok-',''),base=el.dataset.asokComponent,st=el.dataset.asokState;"
                "send({op:'join',cid:cid,name:base,state:st});"
                "['click','input','change','submit','keyup','keydown'].forEach(function(ev){"
                "el.querySelectorAll('[ws-'+ev+']').forEach(function(n){"
                "const attr=n.getAttribute('ws-'+ev),parts=attr.split('.'),meth=parts[0],mods=parts.slice(1);"
                "const handler=function(e){"
                "if(mods.includes('prevent'))e.preventDefault();"
                "if(mods.includes('stop'))e.stopPropagation();"
                "if(mods.includes('enter')&&e.key!=='Enter')return;"
                "const val=n.value,msg={op:'call',cid:cid,method:meth,val:val};"
                "const deb=mods.find(m=>m.startsWith('debounce'));"
                "if(deb){const ms=parseInt(deb.split('-')[1])||300;clearTimeout(timers[n]);"
                "timers[n]=setTimeout(function(){send(msg,n)},ms)}else{send(msg,n)}"
                "};"
                "n['on'+ev]=handler;"
                "})});"
                "el.querySelectorAll('[ws-model]').forEach(function(n){"
                "const prop=n.getAttribute('ws-model');"
                "n.oninput=function(){send({op:'sync',cid:cid,prop:prop,val:n.value},n)};"
                "});"
                "}"
                "window.Asok=window.Asok||{};window.Asok.init=function(el){if(!el)return;if(el.dataset.asokComponent)init(el);el.querySelectorAll('[data-asok-component]').forEach(init)};"
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',connect);else connect();"
                "})();"
                "</script>"
            )

        # 2. Inject nonce into all existing <script> tags to satisfy strict-dynamic CSP
        if nonce:
            content = re.sub(
                r"<script\b([^>]*?)>",
                lambda m: (
                    f'<script{m.group(1)} nonce="{nonce}">'
                    if "nonce=" not in m.group(1)
                    else m.group(0)
                ),
                content,
                flags=re.IGNORECASE,
            )

        # 3. Handle directives asset injection
        needs_directives = (
            any(
                attr in content
                for attr in [
                    "asok-state",
                    "asok-on:",
                    "asok-text",
                    "asok-show",
                    "asok-hide",
                    "asok-class:",
                    "asok-bind:",
                    "asok-model",
                    "asok-if",
                    "asok-for",
                    "asok-init",
                    "asok-ref",
                    "asok-teleport",
                    "asok-cloak",
                ]
            )
            and not is_block
            and not getattr(request, "_asok_directives_done", False)
        )
        if needs_directives:
            request._asok_directives_done = True
            request._asok_pending_styles += (
                f'<style nonce="{nonce}">[asok-cloak]{{display:none!important}}</style>'
            )
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                "(function(){'use strict';const w=new WeakMap();"
                "const st=new Proxy({},{get(t,p){return t[p]},set(t,p,v){t[p]=v;document.querySelectorAll('[asok-state]').forEach(el=>{const c=w.get(el);if(c)us(el)});return true}});"
                "const fss=(el)=>{while(el&&el!==document.documentElement){if(w.has(el))return el;el=el.parentElement}return null};"
                "const gs=(st,el,ev)=>{const sc=fss(el),c=sc?w.get(sc):{refs:{}};return[st,window.Asok.store,el,ev,c.refs||{},f=>Promise.resolve().then(f)]};"
                "const se=(ex,st,el)=>{try{return(new Function('$','$store','$el','$event','$refs','$nextTick','with($){return('+ex+')}'))(...gs(st,el))}catch(e){}};"
                "const es=(sm,st,ev,el)=>{try{const c=sm.includes(';')||sm.includes('if')||sm.includes('return');return(new Function('$','$store','$el','$event','$refs','$nextTick','with($){'+(c?sm:'return('+sm+')')+'}'))(...gs(st,el,ev))}catch(e){}};"
                "const ub=(el,st)=>{if(!el||!st)return;const at=el.getAttribute.bind(el),tr=at('asok-transition');"
                "if(el.hasAttribute('asok-text')){const v=se(at('asok-text'),st,el);if(v!==undefined)el.textContent=String(v)}"
                "if(el.hasAttribute('asok-html')){const v=se(at('asok-html'),st,el);if(v!==undefined)el.innerHTML=String(v).replace(/<script\\b[^<]*(?:(?!<\\/script>)<[^<]*)*<\\/script>/gi,'')}"
                "if(el.hasAttribute('asok-show')){const v=se(at('asok-show'),st,el);if(v){if(el.style.display==='none')el._st=Date.now();el.style.display='';if(tr)el.classList.add(...tr.split(' '));el.setAttribute('data-show-active','');if(tr)el.addEventListener('transitionend',()=>el.classList.remove(...tr.split(' ')),{once:true})}else{if(tr){el.classList.add(...tr.split(' '));el.addEventListener('transitionend',()=>{{el.style.display='none';el.classList.remove(...tr.split(' '))}},{once:true})}else el.style.display='none';el.removeAttribute('data-show-active')}}"
                "if(el.hasAttribute('asok-hide')){const v=se(at('asok-hide'),st,el);if(!v){el.style.display='';if(tr)el.classList.add(...tr.split(' '));el.removeAttribute('data-hide-active');if(tr)el.addEventListener('transitionend',()=>el.classList.remove(...tr.split(' ')),{once:true})}else{if(tr){el.classList.add(...tr.split(' '));el.addEventListener('transitionend',()=>{{el.style.display='none';el.classList.remove(...tr.split(' '))}},{once:true})}else el.style.display='none';el.setAttribute('data-hide-active','')}}"
                "Array.from(el.attributes).forEach(a=>{"
                "if(a.name.startsWith('asok-class:')){const c=a.name.substring(11);el.classList[se(a.value,st,el)?'add':'remove'](c)}"
                "if(a.name.startsWith('asok-bind:')){const n=a.name.substring(10),v=se(a.value,st,el);if(v!==undefined&&v!==null&&v!==false)el.setAttribute(n,String(v));else el.removeAttribute(n)}"
                "})};"
                "const uif=(el,st)=>{let c=el,ok=0;while(c&&(c.hasAttribute('asok-if')||c.hasAttribute('asok-elif')||c.hasAttribute('asok-else'))){c._ai=1;let v=c.hasAttribute('asok-else')?!ok:se(c.getAttribute(c.hasAttribute('asok-if')?'asok-if':'asok-elif'),st,c);if(v&&!ok){if(!c._n){const n=c.content.cloneNode(true);c._n=n.firstElementChild;c.parentNode.insertBefore(n,c.nextSibling);w.set(c._n,w.get(el)||{state:st,refs:{}});init(c._n)}ok=1}else if(c._n){c._n.remove();c._n=null}c=c.nextElementSibling}}; "
                "const ufo=(el,st)=>{el._ai=1;const at=el.getAttribute('asok-for'),[v,l]=at.split(' in '),items=se(l,st,el)||[];if(!el._m){el._m=document.createComment('for');el.parentNode.insertBefore(el._m,el.nextSibling)} (el._ns||[]).forEach(n=>n.remove());el._ns=[];items.forEach((it,i)=>{const n=el.content.cloneNode(true),child=n.firstElementChild,sub=rpx({[v]:it,index:i},()=>us(fss(el)),st);w.set(child,{state:sub,refs:{},cleanup:[]});el.parentNode.insertBefore(n,el._m);el._ns.push(child);init(child)})};"
                "const us=(sc,rt=1)=>{const c=w.get(sc);if(!c)return;ub(sc,c.state);sc.querySelectorAll('*').forEach(el=>{if(el._uv)el._uv();if(el.tagName==='TEMPLATE'){if(el.hasAttribute('asok-if'))uif(el,c.state);if(el.hasAttribute('asok-for'))ufo(el,c.state);return} let p=el.parentElement;while(p&&p!==sc){if(p&&p.hasAttribute('asok-state'))return;p=p.parentElement}ub(el,c.state)});if(rt&&(c._ts||[]).forEach(t=>us(t,0)))return};"
                "const rpx=(obj,cb,st)=>{if(!obj||typeof obj!=='object'||obj._isProxy)return obj;return new Proxy(obj,{get(t,p){if(p==='_isProxy')return true;const v=(p in t)?t[p]:(st?st[p]:undefined);if(typeof v==='function'){if(['push','pop','splice','shift','unshift','reverse','sort'].includes(p))return(...args)=>{const r=v.apply(t,args);cb();return r};return v.bind(t)}return rpx(v,cb,st)},has(t,p){return p in t||(st&&p in st)},set(t,p,v){if(p in t){if(t[p]===v)return true;t[p]=v;cb();return true}if(st){if(st[p]===v)return true;st[p]=v;return true}if(t[p]===v)return true;t[p]=v;cb();return true}})};"
                "const is=(el)=>{if(el._ai)return;const a=el.getAttribute('asok-state');try{const s=rpx(se(a||'{}',{},el)||{},()=>us(el));w.set(el,{state:s,cleanup:[],refs:{},_ts:[]});el._ai=1;if(el.hasAttribute('asok-init'))es(el.getAttribute('asok-init'),s,null,el);us(el)}catch(e){}};"
                "const im=(el)=>{if(el._ami)return;const m=el.getAttribute('asok-model'),sc=fss(el);if(!m||!sc)return;const s=w.get(sc).state;el._ami=1;"
                "el._uv=()=>{const v=s[m]||'';if(el.value!==String(v)&&document.activeElement!==el){if(el.type==='checkbox')el.checked=!!v;else if(el.type==='radio')el.checked=el.value===v;else el.value=v}};"
                "el._uv();const h=()=>{if(el.type==='checkbox')s[m]=el.checked;else if(el.type==='radio'){if(el.checked)s[m]=el.value}else s[m]=el.value};"
                "el.addEventListener('input',h);el.addEventListener('change',h);w.get(sc).cleanup.push(()=>{el.removeEventListener('input',h);el.removeEventListener('change',h)})};"
                "const ie=(el)=>{if(el._aei)return;const sc=fss(el);if(!sc)return;const s=w.get(sc).state;el._aei=1;Array.from(el.attributes).forEach(a=>{if(!a.name.startsWith('asok-on:'))return;"
                "const en=a.name.substring(8),st=a.value,[ev,...mods]=en.split('.'),h=(e)=>{if(mods.includes('prevent'))e.preventDefault();if(mods.includes('stop'))e.stopPropagation();"
                "if(mods.some(m=>['enter','escape','space','tab'].includes(m))&&!mods.some(m=>e.key.toLowerCase()===m))return;es(st,s,e,el)};"
                "if(mods.includes('outside')){const oh=(e)=>{if(el.offsetWidth>0&&!el.contains(e.target)&&(!el._st||Date.now()-el._st>50))h(e)};document.addEventListener('click',oh);w.get(sc).cleanup.push(()=>document.removeEventListener('click',oh))}else{"
                "const deb=mods.find(m=>m.startsWith('debounce')),ms=deb?parseInt(deb.split('-')[1])||300:0;if(ms){let t;const dh=(e)=>{clearTimeout(t);t=setTimeout(()=>h(e),ms)};el.addEventListener(ev,dh);w.get(sc).cleanup.push(()=>el.removeEventListener(ev,dh))}"
                "else{el.addEventListener(ev,h);w.get(sc).cleanup.push(()=>el.removeEventListener(ev,h))}}});};"
                "const init=(r=document)=>{const els=r===document?document.querySelectorAll('*'):[r,...r.querySelectorAll('*')];els.forEach(el=>{if(el.hasAttribute('asok-state'))is(el);if(el.hasAttribute('asok-ref')&&!el._ari){const sc=fss(el);if(sc){w.get(sc).refs[el.getAttribute('asok-ref')]=el;el._ari=1}}"
                "if(el.hasAttribute('asok-teleport')&&!el._ati){const t=el.getAttribute('asok-teleport'),tg=document.querySelector(t),sc=fss(el);if(tg&&sc){const ctx=w.get(sc),n=el.content.cloneNode(true),child=n.firstElementChild;w.set(child,{state:ctx.state,refs:ctx.refs,cleanup:[],_ts:[]});ctx._ts.push(child);tg.appendChild(n);init(child);el._ati=1;el.style.display='none'}} if(el.tagName==='TEMPLATE' && !el._ai){const sc=fss(el);if(sc){const s=w.get(sc).state;if(el.hasAttribute('asok-if'))uif(el,s);if(el.hasAttribute('asok-for'))ufo(el,s)}}});els.forEach(el=>{const sc=fss(el);if(sc)ub(el,w.get(sc).state);if(el.hasAttribute('asok-model'))im(el);if(Array.from(el.attributes).some(a=>a.name.startsWith('asok-on:')))ie(el)});if(r===document)document.querySelectorAll('[asok-cloak]').forEach(e=>e.removeAttribute('asok-cloak'))};"
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>init());else init();"
                "if(window.Asok){const oi=window.Asok.init;window.Asok.init=(el)=>{if(oi)oi(el);init(el)}}"
                "document.addEventListener('asok:success',e=>{if(e.detail&&e.detail.target)init(e.detail.target)});window.Asok=window.Asok||{};window.AsokDirectives={init,version:'1.0.0'};window.Asok.store=st;})()"
                "</script>"
            )

        # 6. Live Reload (DEBUG only)
        if (
            self.config.get("DEBUG")
            and not is_block
            and not getattr(request, "_asok_reload_done", False)
        ):
            request._asok_reload_done = True
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                '(function(){let m="";setInterval(function(){'
                'fetch("/__reload").then(function(r){return r.text()})'
                ".then(function(t){if(m&&m!==t)location.reload();m=t})"
                '.catch(function(){m=""})},1000)})()</script>'
            )

        # Final Injection of accumulated scripts
        scripts = request._asok_pending_scripts
        if scripts and not getattr(request, "_asok_scripts_done", False):
            # 1. Best case: Inject before </body>
            if "</body>" in content.lower():
                request._asok_scripts_done = True
                request._asok_pending_scripts = ""  # Clear

                def inject_scripts(m):
                    return scripts + m.group(1)

                return re.sub(
                    r"(</body>)", inject_scripts, content, flags=re.I, count=1
                )

            # 2. For fragments or final chunks (when stream=False)
            if not stream:
                # Check for clear closing tags first
                is_end = (
                    "</html>" in content.lower() or "</template>" in content.lower()
                )

                if is_end:
                    request._asok_scripts_done = True
                    request._asok_pending_scripts = ""  # Clear
                    return content + "\n" + scripts

                # If no clear end tag, check if we are currently inside a tag
                # (i.e. there is a '<' that hasn't been closed by a '>')
                stripped = content.strip()
                inside_tag = re.search(r"<[^>]*$", content)

                # ALSO check if we are a continuation of a tag from a previous chunk
                # (i.e. we don't start with '<' but we have a '>')
                is_continuation = (
                    stripped and not stripped.startswith("<") and ">" in stripped
                )

                request._asok_scripts_done = True
                request._asok_pending_scripts = ""  # Clear

                if inside_tag or is_continuation:
                    # We are in the middle of a tag! Prepend to avoid breaking it.
                    return scripts + content
                else:
                    # We seem to be between tags. Safe to append.
                    return content + "\n" + scripts

        return content

    # ── Security headers ──────────────────────────────────────

    _DEFAULT_SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    def _cors_allowed(self, origin: str) -> bool:
        """Check whether the given Origin is allowed by CORS_ORIGINS config.

        Accepts CORS_ORIGINS as "*", a comma-separated string, or an iterable.
        Performs exact-match comparison on the full origin string.
        """
        cors_origins = self.config.get("CORS_ORIGINS")
        if not cors_origins:
            return False
        if cors_origins == "*":
            return True
        if isinstance(cors_origins, str):
            allowed = [o.strip() for o in cors_origins.split(",") if o.strip()]
        else:
            try:
                allowed = [str(o).strip() for o in cors_origins]
            except TypeError:
                return False
        return bool(origin) and origin in allowed

    def _security_headers(self, nonce: Optional[str] = None) -> list[tuple[str, str]]:
        """Generate common security headers (HSTS, CSP, etc.)."""
        sec = self.config.get("SECURITY_HEADERS", True)
        if sec is False:
            return []
        base = dict(self._DEFAULT_SECURITY_HEADERS)

        # CSP with WebSocket (connect-src) support
        ws_port = self.config.get("WS_PORT", 8001)
        # Narrow connect-src to self and local WS, while allowing broad ws:/wss:
        # only if necessary (restricting 'self' or app domain is safer)
        csp = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' ws://127.0.0.1:{ws_port} ws://localhost:{ws_port} ws://0.0.0.0:{ws_port};"
        )
        if nonce:
            # Use strict-dynamic to allow scripts loaded by our trusted reactive engine
            csp += f"; script-src 'self' 'nonce-{nonce}' 'unsafe-eval' 'strict-dynamic' https: http:;"
        else:
            csp += "; script-src 'self' 'unsafe-eval' 'unsafe-inline';"

        base["Content-Security-Policy"] = csp

        if isinstance(sec, dict):
            for k, v in sec.items():
                if v is None:
                    base.pop(k, None)
                else:
                    base[k] = v
        return list(base.items())

    # ── WSGI entry point ─────────────────────────────────────

    def __call__(
        self, environ: dict[str, Any], start_response: Callable
    ) -> list[bytes]:
        """Main WSGI entry point for the Asok framework.

        Handles the full request lifecycle:
        1. Environment setup & Request instantiation
        2. Routing & Static file dispatching
        3. Middleware execution
        4. Page/Action handler execution
        5. Template rendering & Asset injection (CSRF, JS)
        6. Response header finalized (Cookies, Security, CORS)
        7. Gzip compression (if enabled)
        """
        environ["asok.root"] = self.root_dir
        environ["asok.app"] = self
        environ["asok.secret_key"] = self.config.get("SECRET_KEY")

        # Debug Log
        with open("asok_debug.log", "a") as f:
            f.write(
                f"\n[{environ.get('REQUEST_METHOD')}] {environ.get('PATH_INFO')} (DEBUG={self.config.get('DEBUG')})\n"
            )

        request = Request(environ)
        path_info = request.path

        # Security nonce
        self.nonce = secrets.token_urlsafe(16)
        request._nonce = self.nonce

        # Force session loading early (session is lazy-loaded, but we need it before headers)
        _ = request.session

        # HEAD support: treat as GET, strip body at the end
        is_head = request.method == "HEAD"
        if is_head:
            request.method = "GET"

        # Reject oversized requests early
        if getattr(request, "_body_rejected", False):
            start_response("413 Payload Too Large", [("Content-Type", "text/plain")])
            return [b"Request body too large"]

        # CORS pre-flight (must be handled before routing/CSRF)
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

        # Health check endpoint
        if path_info == "/__health":
            body = b'{"status":"ok"}'
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]

        # Live reload endpoint (DEBUG only)
        if path_info == "/__reload" and self.config.get("DEBUG"):
            mtime = self._get_src_mtime()
            body = str(mtime).encode()
            start_response(
                "200 OK",
                [("Content-Type", "text/plain"), ("Cache-Control", "no-cache")],
            )
            return [body]

        # Admin module dispatch (intercepts /admin/*)
        admin = getattr(self, "_admin", None)
        if admin and (
            path_info == admin.prefix or path_info.startswith(admin.prefix + "/")
        ):
            try:
                content_str = admin.dispatch(request)
            except RedirectException as redir:
                headers = [("Location", redir.url)]
                headers += self._cookie_headers(request, environ)
                start_response("302 Found", headers)
                return [b""]
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

        # Native API Documentation
        if self.config.get("DOCS", False):
            from .api import handle_docs_request

            res = handle_docs_request(self, request)
            if res:
                if isinstance(res, bytes):
                    output = res
                elif isinstance(res, str):
                    output = res.encode("utf-8")
                else:
                    output = str(res).encode("utf-8")
                headers = [("Content-Type", request.content_type)]
                headers += self._cookie_headers(request, environ)
                headers.append(("Content-Length", str(len(output))))
                start_response(request.status, headers)
                return [output]

        # Static Assets in partials/
        parts = path_info.split("/")
        # Fast path: strip empty segments, check first real segment
        parts = [p for p in parts if p]
        if parts and parts[0] in self._static_dirs:
            static_path = os.path.abspath(os.path.join(self._partials_path, *parts))
            if not static_path.startswith(
                os.path.abspath(self._partials_path) + os.sep
            ):
                body = self._render_error_page(request, 403)
                start_response(
                    "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
                )
                return [body.encode("utf-8")]
            result = self._serve_static(static_path, start_response, environ)
            if result is not None:
                return result

        # Routing (cached in production)
        page_file, route_params = self._resolve_route(parts)

        with open("asok_debug.log", "a") as f:
            f.write(f"  Resolved: {page_file} (Params: {route_params})\n")

        request.params.update(route_params)
        request._current_page_file = page_file

        # 0. Identify Page and Scoped Assets
        if page_file:
            # Derive page_id from relative path (e.g. blog/post -> blog-post)
            try:
                pages_root = os.path.join(self.root_dir, self.dirs["PAGES"])
                rel = os.path.relpath(page_file, pages_root)
                base_name = os.path.splitext(rel)[0]

                # Normalize index pages
                if base_name == self.config["INDEX"] or base_name.endswith(
                    os.sep + self.config["INDEX"]
                ):
                    base_name = os.path.dirname(rel) or "index"

                request.page_id = (
                    base_name.replace(os.sep, "-").replace(".", "-").strip("-")
                )
                if not request.page_id:
                    request.page_id = "index"

                with open("asok_debug.log", "a") as f:
                    f.write(f"  Page ID: {request.page_id}\n")

                # Look for companion assets
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
                headers = [("Content-Type", "text/html; charset=utf-8")]
                headers += self._cookie_headers(request, environ)
                start_response("404 Not Found", headers)
                return SmartStreamer(body, request, self)

            # Ensure assets and nonces are injected even for error strings
            body = self._inject_assets(body, request, getattr(request, "nonce", ""))

            headers = [("Content-Type", "text/html; charset=utf-8")]
            headers += self._cookie_headers(request, environ)
            start_response("404 Not Found", headers)
            return [body.encode("utf-8")]

        # CSRF
        if self.config.get("CSRF") and request.method in (
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        ):
            token = request.form.get("csrf_token") or environ.get("HTTP_X_CSRF_TOKEN")

            # 1. Signature validation
            is_valid = token and secrets.compare_digest(
                str(token), str(request.csrf_token_value)
            )

            # 2. Origin validation (Defense-in-depth)
            origin = environ.get("HTTP_ORIGIN") or environ.get("HTTP_REFERER")
            if is_valid and origin:
                try:
                    parsed = urlparse(origin)
                except Exception:
                    parsed = None
                expected_base = f"{request.scheme}://{request.host}"
                if (
                    not parsed
                    or parsed.scheme != request.scheme
                    or parsed.netloc != request.host
                ):
                    # Potential CSRF or Cross-Origin form submission
                    is_valid = False
                    logger.warning(
                        "CSRF Origin mismatch: %s vs %s", origin, expected_base
                    )

            if not is_valid:
                logger.warning(
                    "CSRF validation failed: %s %s from %s",
                    request.method,
                    request.path,
                    request.ip,
                )
                body = self._render_error_page(request, 403)
                start_response(
                    "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
                )
                return [body.encode("utf-8")]
            # Rotate CSRF token after successful validation
            request.csrf_token_value = secrets.token_hex(32)

        # Execution
        content_str = ""
        try:
            module = None
            if page_file.endswith(".py"):
                module = self._load_module(page_file)

            tpl_root = self._tpl_root

            def core_layer(req):
                if module:
                    # Detect available methods for potential 405
                    supported = []
                    # Check for explicit METHODS list
                    if hasattr(module, "METHODS") and isinstance(
                        module.METHODS, (list, tuple)
                    ):
                        supported.extend([m.upper() for m in module.METHODS])
                    # Check for method-specific functions
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

                    # 1. Form Action Dispatcher (POST only)
                    if req.method == "POST":
                        action_name = req.form.get("_action") or req.params.get(
                            "action"
                        )
                        if action_name:
                            # Security: validate action name (alphanumeric + underscore only)
                            if (
                                not action_name.replace("_", "")
                                .replace("-", "")
                                .isalnum()
                            ):
                                action_name = None
                            # Security: block private actions (starting with _)
                            elif action_name.startswith("_"):
                                action_name = None

                        if action_name:
                            action_func = getattr(module, f"action_{action_name}", None)
                            if callable(action_func):
                                res = action_func(req)
                                if res is None:
                                    req.abort(
                                        500,
                                        f"Action handler 'action_{action_name}' in {page_file} returned None. "
                                        "Ensure your action returns request.html(), request.json(), or calls request.redirect().",
                                    )
                                return res

                    # 2. Try method-specific function (get, post, etc.)
                    method_func = getattr(module, req.method.lower(), None)
                    if callable(method_func):
                        res = method_func(req)
                        if res is None:
                            req.abort(
                                500,
                                f"Method function '{req.method.lower()}' in {page_file} returned None.",
                            )
                        return res

                    # 3. Fallback to render()
                    if hasattr(module, "render"):
                        res = module.render(req)
                        if res is None:
                            if supported and req.method not in supported:
                                req.method_not_allowed(supported)
                            req.abort(
                                500,
                                f"render() in {page_file} returned None. Check your logic.",
                            )
                        return res

                    # 4. Fallback to CONTENT constant
                    if hasattr(module, "CONTENT"):
                        return module.CONTENT

                    # 5. Method not allowed (no render() and method not in supported)
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

                # If we reach here, no handler matched (and no template file error occurred)
                req.status = "404 Not Found"
                return "<h1>404 Not Found</h1><p>The requested route does not provide a valid handler.</p>"

            chain = self._get_middleware_chain(core_layer)
            content_str = chain(request)

            # Override default error responses with custom pages if available
            status_code = request.status.split(" ")[0]
            is_default_error = False
            if isinstance(content_str, str) and "<h1>" in content_str:
                is_default_error = True
            elif status_code.startswith(("4", "5")) and not isinstance(
                content_str, str
            ):
                # If it's an error and not a string, it might be a missing route generator
                is_default_error = True

            if status_code.startswith(("4", "5")) and is_default_error:
                content_str = self._render_error_page(request, int(status_code))
        except AbortException as abort:
            request.status = Request._STATUS_MAP.get(
                abort.status, f"{abort.status} Unknown"
            )
            content_str = self._render_error_page(
                request, abort.status, message=abort.message
            )
            # Re-run assets injection and minification on the error page
            # (The code at the end of __call__ will handle this if we let it fall through,
            # but AbortException usually implies we stop here).
            # Actually, just let it fall through would be easier but we are in a 'try' block.
            # Best is to set content_str and let the rest of the method handle it.
            pass
        except RedirectException as redir:
            headers = [("Location", redir.url)]
            headers += self._cookie_headers(request, environ)
            status_map = {
                301: "301 Moved Permanently",
                302: "302 Found",
                303: "303 See Other",
                307: "307 Temporary Redirect",
            }
            start_response(
                status_map.get(redir.status, f"{redir.status} Found"), headers
            )
            return [b""]
        except Exception as e:
            import traceback

            logger.error("Unhandled Exception: %s\n%s", str(e), traceback.format_exc())
            if self.config.get("DEBUG"):
                start_response(
                    "500 Internal Server Error", [("Content-Type", "text/plain")]
                )
                return [f"ERROR: {str(e)}\n\n{traceback.format_exc()}".encode()]
            body = self._render_error_page(request, 500)
            body = self._inject_assets(body, request, getattr(request, "nonce", ""))

            start_response(
                "500 Internal Server Error",
                [("Content-Type", "text/html; charset=utf-8")],
            )
            return [body.encode("utf-8")]

        # Streamed file response (large files)
        if "asok.stream_file" in environ:
            stream_path = environ["asok.stream_file"]
            headers = [("Content-Type", request.content_type)]
            headers += environ.get("asok.extra_headers", [])
            headers += self._cookie_headers(request, environ)
            start_response(request.status, headers)
            if is_head:
                return [b""]

            def _file_iter(path, chunk_size=65536):
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk

            return _file_iter(stream_path)

        # Binary response (send_file)
        if "asok.binary_response" in environ:
            output = environ["asok.binary_response"]
            headers = [("Content-Type", request.content_type)]
            headers += environ.get("asok.extra_headers", [])
            headers += self._cookie_headers(request, environ)
            headers.append(("Content-Length", str(len(output))))
            start_response(request.status, headers)
            return [b""] if is_head else [output]

        # Handle Generators (Native Streaming)
        if inspect.isgenerator(content_str):
            headers = [("Content-Type", request.content_type)]
            headers += self._cookie_headers(request, environ)
            headers += self._security_headers(nonce=getattr(request, "nonce", None))

            # Check for Gzip
            use_gzip = (
                self.config.get("GZIP", False)
                and "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "").lower()
            )
            if use_gzip:
                headers.append(("Content-Encoding", "gzip"))
            start_response(request.status, headers)
            return SmartStreamer(content_str, request, self)

        # Standard String Response
        if "text/html" in request.content_type:
            content_str = self._inject_assets(
                content_str, request, getattr(request, "nonce", None)
            )

            # HTML Minification
            should_minify = self.config.get("HTML_MINIFY")
            if should_minify is None:
                should_minify = not self.config.get("DEBUG")

            if should_minify:
                content_str = minify_html(str(content_str))

        output = str(content_str).encode("utf-8")

        # Response headers
        headers = [("Content-Type", request.content_type)]
        headers += self._cookie_headers(request, environ)
        headers += self._security_headers(nonce=getattr(request, "nonce", None))
        headers += environ.get("asok.extra_headers", [])
        headers += request.response_headers
        # Expose new CSRF token for JS block swap after rotation
        if environ.get("HTTP_X_BLOCK"):
            headers.append(("X-CSRF-Token", request.csrf_token_value))
            headers.append(("Access-Control-Expose-Headers", "X-CSRF-Token"))

        # CORS
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

        # Gzip compression for standard responses
        if (
            self.config.get("GZIP", False)
            and len(output) > self.config.get("GZIP_MIN_SIZE", 500)
            and "gzip" in environ.get("HTTP_ACCEPT_ENCODING", "").lower()
        ):
            buf = io.BytesIO()
            with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
                f.write(output)
            output = buf.getvalue()
            headers.append(("Content-Encoding", "gzip"))
            headers.append(("Vary", "Accept-Encoding"))

        headers.append(("Content-Length", str(len(output))))
        start_response(request.status, headers)
        return [b""] if is_head else [output]
