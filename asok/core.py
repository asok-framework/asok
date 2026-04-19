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
from typing import Any, Callable, Optional, Union
from urllib.parse import quote, urlparse

from .exceptions import AbortException, RedirectException
from .orm import Model
from .request import Request
from .session import SessionStore
from .templates import render_template_string
from .utils.css import scope_css
from .utils.js import scope_js
from .utils.minify import minify_html

logger = logging.getLogger("asok.security")


class SmartStreamer:
    """Helper to stream HTML chunks with injection and Gzip support."""

    def __init__(self, generator, request, app, use_gzip=False):
        self.generator = generator
        self.request = request
        self.app = app
        self.use_gzip = use_gzip
        self.injected = False
        self.nonce = secrets.token_urlsafe(16)
        self.buffer = b""

    def __iter__(self):
        gzip_obj = None
        if self.use_gzip:
            buf = io.BytesIO()
            gzip_obj = gzip_mod.GzipFile(fileobj=buf, mode="wb")

        def write(chunk):
            if gzip_obj:
                gzip_obj.write(chunk)
                gzip_obj.flush()
                val = buf.getvalue()
                buf.seek(0)
                buf.truncate()
                return val
            return chunk

        for chunk_str in self.generator:
            if not chunk_str:
                continue
            if not self.injected and "text/html" in self.request.content_type:
                # Inject CSRF and Scripts in the first viable HTML chunk
                chunk_str = self.app._inject_assets(
                    chunk_str, self.request, self.nonce, stream=True
                )
                self.injected = True

            yield write(chunk_str.encode("utf-8"))

        if gzip_obj:
            gzip_obj.close()
            yield buf.getvalue()


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
        cache_key = "/".join(str(p) for p in parts)
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
                    if ext == ".py":
                        mod = self._load_module(error_file)
                        if hasattr(mod, "render"):
                            return mod.render(request)
                    else:
                        content = self._read_template(error_file)
                        ctx = {
                            "request": request,
                            "__": request.__,
                            "static": request.static,
                            "get_flashed_messages": request.get_flashed_messages,
                            "error_message": message,
                        }
                        return render_template_string(
                            content, ctx, root_dir=self._tpl_root
                        )
                except Exception:
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
        # Auto-save session if modified
        if request._session is not None and request._session.modified:
            sid = request._session.sid
            self._session_store.save(sid, request._session)
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
        self, content: str, request: Request, nonce: str, stream: bool = False
    ) -> str:
        """Inject required CSRF tags, metadata, and scripts into the HTML response."""
        if not isinstance(content, str):
            return content

        scripts = ""

        # 0. SEO Metadata (Title, Metas, Links)
        meta_html = ""
        meta_obj = getattr(request, "meta", None)
        if meta_obj:
            # 0.1 Handle Title (Replacement-aware)
            if meta_obj._title:
                # Remove any existing title tags to ensure override
                content = re.sub(
                    r"<title>.*?</title>", "", content, flags=re.IGNORECASE | re.DOTALL
                )
                meta_html += f"    <title>{_html.escape(meta_obj._title)}</title>\n"

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
            if "<head>" in content:
                content = content.replace("<head>", "<head>\n" + meta_html, 1)
            elif "<head " in content:
                idx = content.find("<head ")
                end = content.find(">", idx)
                if end != -1:
                    content = content[: end + 1] + "\n" + meta_html + content[end + 1 :]

        # 0.5 Scoped Assets (CSS/JS) and Page ID
        if request.page_id:
            # Inject data-asok-page attribute into <body>
            body_match = re.search(r"<body(\s+[^>]*)?>", content, re.I)
            if body_match:
                orig_body = body_match.group(0)
                if "data-asok-page" not in orig_body:
                    if orig_body.endswith("/>"):  # Unlikely for body but...
                        new_body = (
                            orig_body[:-2] + f' data-asok-page="{request.page_id}"/>'
                        )
                    else:
                        new_body = (
                            orig_body[:-1] + f' data-asok-page="{request.page_id}">'
                        )
                    content = content.replace(orig_body, new_body, 1)
            else:
                # For block requests, inject a marker that the SPA engine can read
                content += f'<div id="asok-page-id-marker" data-page-id="{request.page_id}" style="display:none"></div>'

            # Inject Scoped CSS
            if request.scoped_assets.get("css"):
                try:
                    with open(request.scoped_assets["css"], "r", encoding="utf-8") as f:
                        raw_css = f.read()
                    scoped_css_content = scope_css(raw_css, request.page_id)
                    style_tag = f'\n<style id="asok-scoped-css">\n{scoped_css_content}\n</style>\n'
                    # Inject at end of head to ensure higher specificity
                    if "</head>" in content:
                        content = content.replace("</head>", style_tag + "</head>", 1)
                    else:
                        # For block requests, just append it
                        content += style_tag
                except Exception:
                    pass

            # Prepare Scoped JS
            if request.scoped_assets.get("js"):
                try:
                    with open(request.scoped_assets["js"], "r", encoding="utf-8") as f:
                        raw_js = f.read()
                    scoped_js_content = scope_js(raw_js)
                    scripts += f'\n<script id="asok-scoped-js" nonce="{nonce}">\n{scoped_js_content}\n</script>\n'
                except Exception:
                    pass

        # 1. CSRF Meta Tag
        csrf_meta = f'<meta name="csrf-token" content="{request.csrf_token_value}">'
        if "<head>" in content:
            content = content.replace("<head>", "<head>" + csrf_meta, 1)
        elif "<head " in content:
            idx = content.find("<head ")
            end = content.find(">", idx)
            if end != -1:
                content = content[: end + 1] + csrf_meta + content[end + 1 :]

        # 2. Asok Transitions (Independent & Opt-in)
        needs_transition = "asok-transition" in content
        if needs_transition:
            # Shared transition styles
            scripts += (
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

        # 3. Reactive Engine & Block Swap Logic
        needs_reactive = any(
            attr in content
            for attr in ["data-block", "data-sse", "data-url", "data-method"]
        )
        if needs_reactive:
            scripts += (
                f'<script nonce="{nonce}">'
                "(function(){const ca={};"
                "function ct(){const m=document.querySelector('meta[name=csrf-token]');return m?m.content:''}"
                "function qb(s){if(!s)return null;let t;try{t=document.querySelector(s)}catch(e){}"
                "if(!t&&/^[a-zA-Z0-9_-]+$/.test(s))t=document.getElementById(s);return t}"
                "function doSwap(t,h,mode){ (window.Asok&&window.Asok.swap)?window.Asok.swap(t,h,mode):t.innerHTML=h; }"
                "function sw(url,b,sel,mode,opts,src){"
                "const h=Object.assign({'X-Block':b,'X-CSRF-Token':ct()},opts.headers||{});"
                "opts.headers=h;"
                "const key=url+b;const p=ca[key]?Promise.resolve(ca[key]):fetch(url,opts).then(function(r){"
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
                "const scs=d.querySelector('#asok-scoped-css');if(scs){const e=document.getElementById('asok-scoped-css');if(e)e.remove();document.head.appendChild(scs)}"
                "const scj=d.querySelector('#asok-scoped-js');if(scj){const e=document.getElementById('asok-scoped-js');if(e)e.remove();const ns=document.createElement('script');ns.id='asok-scoped-js';if(scj.nonce)ns.nonce=scj.nonce;ns.textContent=scj.textContent;document.body.appendChild(ns)}"
                "const pid=d.querySelector('#asok-page-id-marker');if(pid){document.body.dataset.asokPage=pid.dataset.pageId}"
                "if(src&&src.dataset&&src.dataset.pushUrl!==undefined){"
                "const pu=src.dataset.pushUrl||url;"
                "history.pushState({b:b,sel:sel,mode:mode,url:url},'',pu)"
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

        # 4. WebSocket connectivity helper & Alive Engine
        needs_alive = "data-asok-component" in content or "ws-" in content
        if needs_alive:
            ws_port = self.config.get("WS_PORT", 8001)
            scripts += (
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
            scripts += (
                f'<script nonce="{nonce}">'
                "(function(){"
                "let ws;const timers={};function connect(){"
                "ws=window.asokWS('/asok/live');"
                "ws.onopen=function(){document.querySelectorAll('[data-asok-component]').forEach(init)};"
                "ws.onmessage=function(e){"
                "const d=JSON.parse(e.data);if(d.op==='render'){"
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
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',connect);else connect();"
                "})();"
                "</script>"
            )

        # 5. Live Reload (DEBUG only)
        if self.config.get("DEBUG"):
            scripts += (
                f'<script nonce="{nonce}">'
                '(function(){let m="";setInterval(function(){'
                'fetch("/__reload").then(function(r){return r.text()})'
                ".then(function(t){if(m&&m!==t)location.reload();m=t})"
                '.catch(function(){m=""})},1000)})()</script>'
            )

        if stream:
            return content + scripts
        if "</body>" in content:
            return content.replace("</body>", scripts + "\n</body>")
        return content + scripts

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
            f"default-src 'self'; style-src 'self' 'unsafe-inline'; "
            f"connect-src 'self' ws://127.0.0.1:{ws_port} ws://localhost:{ws_port};"
        )
        if nonce:
            csp += f"; script-src 'self' 'nonce-{nonce}'"
        else:
            csp += "; script-src 'self'"

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
            f.write(f"\n[{environ.get('REQUEST_METHOD')}] {environ.get('PATH_INFO')} (DEBUG={self.config.get('DEBUG')})\n")

        request = Request(environ)
        request.nonce = secrets.token_urlsafe(16)
        path_info = request.path

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
                if base_name == self.config["INDEX"] or base_name.endswith(os.sep + self.config["INDEX"]):
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
                            action_func = getattr(module, f"action_{action_name}", None)
                            if callable(action_func):
                                res = action_func(req)
                                if res is None:
                                    req.abort(
                                        500,
                                        f"Action 'action_{action_name}' in {page_file} returned None.",
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
            if (
                request.status.startswith("403")
                and str(content_str) == "<h1>403 Forbidden</h1>"
            ):
                content_str = self._render_error_page(request, 403)
            elif (
                request.status.startswith("404")
                and str(content_str) == "<h1>404 Not Found</h1>"
            ):
                content_str = self._render_error_page(request, 404)
            elif request.status.startswith("429") and str(content_str).startswith(
                "<h1>429"
            ):
                content_str = self._render_error_page(request, 429)
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
            return SmartStreamer(content_str, request, self, use_gzip=use_gzip)

        # Standard String Response
        if "text/html" in request.content_type:
            content_str = self._inject_assets(content_str, request, request.nonce)

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
