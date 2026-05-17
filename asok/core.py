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
from urllib.parse import quote

from .context import request_context, request_var
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
        self.nonce = request.nonce
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
            # Minify in production (DEBUG=False), skip in development (DEBUG=True)
            should_minify = not self.app.config.get("DEBUG", False)

        def finalize(text):
            if not should_minify or not text:
                return text
            return minify_html(text)

        try:
            # Buffer EVERYTHING to avoid chunking issues with asset injection
            full_content = ""
            for chunk_str in self.generator:
                full_content += chunk_str

            # 1. Minify structure first
            full_content = finalize(full_content)

            # 2. Inject assets on the minified document
            final_content = self.app._inject_assets(
                full_content, self.request, self.nonce, stream=True, only_scripts=False
            )

            encoded = final_content.encode("utf-8")

            # Gzip compression for streaming
            if (
                self.app.config.get("GZIP", False)
                and "gzip"
                in self.request.environ.get("HTTP_ACCEPT_ENCODING", "").lower()
            ):
                buf = io.BytesIO()
                with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
                    f.write(encoded)
                encoded = buf.getvalue()

            yield write(encoded)

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
    Acts as the main entry point for your web application.

    Basic Usage:
        from asok import Asok
        from asok.admin import Admin

        app = Asok()
        Admin(app)

    Initialization:
        The app automatically discovers your project structure by scanning
        the current working directory for a `src/` folder. It loads models,
        middlewares, components, and locales automatically during `setup()`.

    Configuration:
        Values are populated from your `.env` file and accessible via `app.config`.
        Key settings include:
        - DEBUG: Enable hot-reloading and detailed error pages.
        - SECRET_KEY: Used for signing sessions, cookies, and tokens.
        - INDEX: The default filename for directory-based routing (default: "page").

    Middleware:
        Register custom middleware to intercept requests and responses:
        @app.use
        def my_middleware(request, next_handler):
            # Pre-processing
            response = next_handler(request)
            # Post-processing
            # Add framework version diagnostic headers
            response.headers["X-Asok-Version"] = "0.1.6-stabilized"
            response.headers["X-Asok-Debug-Nonce"] = getattr(request, "nonce", "none")

            return response

    Lifecycle Hooks:
        Execute code when the server starts or stops:
        @app.on_startup
        def init_external_service():
            pass

        @app.on_shutdown
        def cleanup():
            pass

    Shared Variables:
        Make data available globally across all templates:
        app.share(site_name="My Awesome App", version="1.0.0")
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
            "DEBUG": False,
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
            "BG_WORKERS": 10,  # Max background threads
        }

        # Lifecycle hooks
        self._on_startup: list[Callable] = []
        self._on_shutdown: list[Callable] = []
        self._tasks: list[Any] = []
        self._executor: Optional[Any] = None

        # Caches (populated in production, bypassed in DEBUG)
        self._route_cache: dict[str, tuple[str, dict[str, str]]] = {}
        self._module_cache: dict[str, Any] = {}
        self._static_cache: dict[str, tuple[bytes, str]] = {}
        self._static_cache_size: int = 0
        self._static_cache_max: int = 50 * 1024 * 1024  # 50 MB max
        self._template_cache: dict[str, str] = {}
        self._middleware_chain: Optional[Callable] = None

        # Initial logger (console default)
        from .logger import get_logger

        self.logger = get_logger("asok", config=self.config)

        self.setup()

    def setup(self) -> None:
        """Configure the application environment, load models, and prepare internal states."""

        src_path = os.path.join(self.root_dir, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # 2. Re-configure Logger after .env loading
        from .logger import get_logger

        self.logger = get_logger("asok", config=self.config)

        # Ensure core directories are Python packages to avoid import errors
        self._ensure_package_dirs(
            self.dirs["MODELS"],
            self.dirs["COMPONENTS"],
            self.dirs["MIDDLEWARES"],
            "src/pages",
            "src/routes",
        )

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

        # 2.6 Determine TOOLBAR mode
        toolbar_val = os.environ.get(
            "ASOK_TOOLBAR", os.environ.get("TOOLBAR", "")
        ).lower()
        if toolbar_val == "false":
            self.config["TOOLBAR"] = False
        elif toolbar_val == "true":
            self.config["TOOLBAR"] = True

        # 3. Security Key (respects DEBUG mode determined above)
        sec_key = os.getenv("SECRET_KEY")
        if not sec_key:
            if self.config.get("DEBUG"):
                # Stable dev key based on project path to survive hot-reloads
                h = hashlib.md5(self.root_dir.encode()).hexdigest()
                sec_key = f"dev-secret-{h}"
                logger.warning(
                    "Running with auto-generated SECRET_KEY (DEBUG mode). "
                    "Set SECRET_KEY in your .env before deploying to production. "
                    "SECURITY WARNING: The auto-generated key is predictable and creates "
                    "session fixation risks. Never use DEBUG mode in production."
                )
            else:
                raise RuntimeError(
                    "SECRET_KEY environment variable is required in production. "
                    "Set it in your .env file or environment: SECRET_KEY=your-secret-key"
                )

        # Validate key length for production
        if not self.config.get("DEBUG") and (not sec_key or len(sec_key) < 32):
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY must be at least 32 characters long in production. "
                "Current key is too weak. Please generate a strong key using 'secrets.token_hex(32)'."
            )

        self.config["SECRET_KEY"] = sec_key
        os.environ["SECRET_KEY"] = sec_key
        self.config.setdefault("WS_PORT", 8001)
        self.config.setdefault(
            "CSP_UNSAFE_EVAL", False
        )  # Default: disabled for security

        # 4. Global Config Overrides from Environment
        # This allows overriding any default config value via .env or shell environment
        for key in list(self.config.keys()):
            # Check both direct name (e.g. MAX_CONTENT_LENGTH) and prefixed (ASOK_MAX_CONTENT_LENGTH)
            env_val = os.environ.get(f"ASOK_{key}", os.environ.get(key))
            if env_val is not None:
                # Attempt to preserve the type of the default value
                current_val = self.config[key]
                if isinstance(current_val, bool):
                    self.config[key] = env_val.lower() in ("true", "1", "yes", "on")
                elif isinstance(current_val, int):
                    try:
                        self.config[key] = int(env_val)
                    except ValueError:
                        pass
                else:
                    self.config[key] = env_val

        # Load Middlewares
        mw_dir = os.path.join(self.root_dir, self.dirs["MIDDLEWARES"])
        if os.path.exists(mw_dir):
            for filename in sorted(os.listdir(mw_dir)):
                if (
                    filename.endswith(".py") or filename.endswith(".pyc")
                ) and not filename.startswith("__"):
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
                if (
                    filename.endswith(".py") or filename.endswith(".pyc")
                ) and not filename.startswith("__"):
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
                            self.models.append(attr)

        # 4. Load Components
        comp_dir = os.path.join(self.root_dir, self.dirs["COMPONENTS"])
        if os.path.exists(comp_dir):
            import sys as _sys

            for filename in sorted(os.listdir(comp_dir)):
                if (
                    filename.endswith(".py") or filename.endswith(".pyc")
                ) and not filename.startswith("__"):
                    filepath = os.path.join(comp_dir, filename)
                    # Use name WITHOUT extension so inspect.getfile() can resolve the path
                    ext_len = 4 if filename.endswith(".pyc") else 3
                    mod_name = f"comp_{filename[:-ext_len]}"
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

    def _ensure_package_dirs(self, *dirs: str) -> None:
        """Create empty __init__.py in directories if they exist but are not Python packages."""
        for d in dirs:
            path = os.path.join(self.root_dir, d)
            if os.path.isdir(path):
                init_file = os.path.join(path, "__init__.py")
                init_file_c = os.path.join(path, "__init__.pyc")
                if not os.path.exists(init_file) and not os.path.exists(init_file_c):
                    try:
                        with open(init_file, "w"):
                            pass
                    except Exception as e:
                        logger.warning(f"Could not create __init__.py in {d}: {e}")

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
        """Run all registered shutdown hooks and stop background tasks."""
        for fn in self._on_shutdown:
            fn()

        # Stop all scheduled background tasks
        for task in self._tasks:
            try:
                task.cancel()
            except Exception:
                pass

        # Shut down the background executor
        if self._executor:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

        if hasattr(self, "_session_store"):
            self._session_store.stop_cleanup_timer()

    def share(self, **kwargs: Any) -> Asok:
        self._shared.update(kwargs)
        return self

    def schedule(
        self,
        interval: str | float,
        fn: Optional[Callable] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Schedule a recurring background task managed by the app.
        Can be used as a method or as a decorator.

        Usage:
            app.schedule("5m", my_cleanup_function)

            @app.schedule("1h")
            def periodic_task():
                ...
        """
        from .scheduler import schedule as _schedule

        def decorator(func: Callable) -> Any:
            task = _schedule(interval, func, *args, **kwargs)
            self._tasks.append(task)
            return task

        if fn is None:
            return decorator

        task = _schedule(interval, fn, *args, **kwargs)
        self._tasks.append(task)
        return task

    def log_info(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an info message via the app's logger."""
        self.logger.info(message, *args, **kwargs)

    def log_error(self, message: str, *args: Any, **kwargs: Any) -> None:
        """Log an error message via the app's logger."""
        self.logger.error(message, *args, **kwargs)

    def background(
        self, fn: Optional[Callable] = None, *args: Any, **kwargs: Any
    ) -> Any:
        """Run a function in a background thread managed by the app.
        Can be used as a method or as a decorator.

        Usage:
            # As a method (immediate execution):
            app.background(my_heavy_function, arg1, key=val)

            # As a decorator (deferred execution):
            @app.background
            def my_async_task(data):
                ...
        """
        from concurrent.futures import ThreadPoolExecutor
        from functools import wraps

        from .background import background as _background

        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.config.get("BG_WORKERS", 10),
                thread_name_prefix=f"asok_app_{id(self)}_",
            )

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*fargs, **fkwargs):
                return _background(func, *fargs, executor=self._executor, **fkwargs)

            return wrapper

        if fn is None:
            return decorator

        # Check if it's being used as a decorator without parens @app.background
        # or as a direct method call app.background(fn, *args)
        if args or kwargs:
            # Direct call with arguments: execute now
            return _background(fn, *args, executor=self._executor, **kwargs)

        # Potential decorator usage @app.background
        # But wait, how do we distinguish @app.background from app.background(fn)?
        # In Python, @app.background calls app.background(fn).
        # To support both, we assume if only 'fn' is provided, it's a decorator.
        # This is standard for decorators that can be used with or without parens.
        return decorator(fn)

    def rate_limit(self, limit: str | int, window: Optional[int] = None, **kwargs):
        """Decorator to apply rate limiting to a specific route.

        Usage:
            @app.rate_limit("5/m")
            def get(request):
                return "Hello"
        """
        from .ratelimit import rate_limit as _rate_limit

        return _rate_limit(limit, window, **kwargs)

    def cache_page(self, ttl: int = 60, key_prefix: str = "page_"):
        """Decorator to cache the HTTP response of a view function.

        Usage:
            @app.cache_page(ttl=300)
            def get(request):
                return "Heavy content"
        """
        from .cache import cache_page as _cache_page

        return _cache_page(ttl, key_prefix)

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

        # Watch root files (.env, wsgi.py)
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
        """Convert a URL segment to a typed parameter (int, float, uuid, slug).

        SECURITY: Limits parameter length to prevent DoS attacks.
        """
        # SECURITY: Reject overly long parameters (max 255 chars)
        MAX_PARAM_LENGTH = 255
        if len(value) > MAX_PARAM_LENGTH:
            if self.config.get("DEBUG"):
                logger.debug(
                    f"Routing: Parameter too long ({len(value)} chars, max {MAX_PARAM_LENGTH}): '{value[:50]}...'"
                )
            return None

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
            for ext in (".py", ".pyc", ".html", ".asok"):
                p = os.path.join(current_base, self.config["INDEX"] + ext)
                if os.path.isfile(p):
                    return p, captured_params
            return None, captured_params

        seg = segments[0]
        remaining = segments[1:]

        # 0. Literal File match (e.g. /about -> about.py or about.html or about.asok)
        if not remaining:
            for ext in (".py", ".pyc", ".html", ".asok"):
                p = os.path.join(current_base, seg + ext)
                if os.path.isfile(p):
                    return p, captured_params

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
        for ext in (".html", ".asok", ".py", ".pyc"):
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

                    if ext in (".py", ".pyc"):
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
                            "title": getattr(request.meta, "_title", f"Error {code}"),
                            "description": getattr(
                                request.meta, "_description", "An error occurred."
                            ),
                            "structured_data": getattr(
                                request.meta, "_structured_data", None
                            ),
                            "meta": request.meta,
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
            # SECURITY: SameSite=Strict provides better CSRF protection than Lax
            cookie = f"asok_sid={signed}; HttpOnly; Path=/; SameSite=Strict; Max-Age={self.config['SESSION_TTL']}"
            if request.scheme == "https":
                cookie += "; Secure"
            headers.append(("Set-Cookie", cookie))
        # Only send CSRF cookie if it's new or changed
        incoming_csrf = request.cookies_dict.get(request._csrf_cookie_name)
        if request.csrf_token_value and request.csrf_token_value != incoming_csrf:
            # SECURITY: SameSite=Strict provides better CSRF protection than Lax
            csrf_cookie = f"{request._csrf_cookie_name}={request.csrf_token_value}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400"
            if request.scheme == "https":
                csrf_cookie += "; Secure"
            headers.append(("Set-Cookie", csrf_cookie))
        if request._new_flashes and not request._new_flashes_consumed:
            # Redirect case: persist new flashes for next request (HMAC-signed)
            signed_flash = self._sign(json.dumps(request._new_flashes))
            # SECURITY: SameSite=Strict provides better CSRF protection than Lax
            headers.append(
                (
                    "Set-Cookie",
                    f"{request._flash_cookie_name}={quote(signed_flash)}; Path=/; HttpOnly; SameSite=Strict",
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

    def _validate_directive_expression(self, expr: str) -> bool:
        """Validate that a directive expression is safe (no code injection).

        Uses a hybrid approach: checks for dangerous patterns, then validates structure.
        SECURITY: Prevents code injection via template directives.

        Note: Supports JavaScript syntax since directives execute client-side.
        """
        import ast
        import re

        expr_stripped = expr.strip()

        # SECURITY: Check for dangerous keywords first (server-side injection attempt)
        DANGEROUS_PATTERNS = [
            # Python server-side injection
            r"\b__import__\b",
            r"\beval\b",
            r"\bexec\b",
            r"\bcompile\b",
            r"\bopen\b\s*\(",
            r"\bfile\b\s*\(",
            r"\b__\w+__\b",  # Dunder methods/attributes
            r"\bglobals\b",
            r"\blocals\b",
            r"\bvars\b",
            r"\bgetattr\b",
            r"\bsetattr\b",
            r"\bdelattr\b",
            r"\bdir\b\s*\(",
            r"\bhelp\b\s*\(",
            # JavaScript client-side dangerous APIs
            r"\bwindow\.fetch\b",
            r'\bfetch\s*\(\s*[\'"]https?://',  # fetch with absolute URL
            r"\bXMLHttpRequest\b",
            r"\bsendBeacon\b",
            r"\bwindow\.location\b",
            r"\bdocument\.location\b",
            r"\blocation\.replace\b",
            r"\blocation\.href\s*=",
            r"\bwindow\.open\b",
            r"\bwindow\.eval\b",
            r"\bdocument\.write\b",
            r"\bdocument\.writeln\b",
            r"\bdocument\.createElement\b",
            r"\.innerHTML\s*=",
            # Constructor-based bypasses (eval alternatives)
            r"\bconstructor\.constructor\b",  # constructor.constructor or .constructor.constructor
            r'\bconstructor\s*\[\s*[\'"]constructor[\'"]\s*\]',
            r'\[\s*[\'"]constructor[\'"]\s*\]\s*\[\s*[\'"]constructor[\'"]\s*\]',
            r"\.concat\.constructor\b",
            r"\bFunction\s*\(",  # Function constructor
            r"\.prototype\b",
            # Template literals with interpolation
            r"`.*\$\{.*\}.*`",
        ]

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, expr_stripped):
                return False

        # For arrow functions, validate the body recursively
        # Arrow functions: x => expr, (a, b) => expr, () => { statements }
        if "=>" in expr_stripped:
            # Extract the body after =>
            parts = expr_stripped.split("=>", 1)
            if len(parts) == 2:
                body = parts[1].strip()
                # Remove block braces if present: { ... } → ...
                if body.startswith("{") and body.endswith("}"):
                    body = body[1:-1].strip()
                # Validate the body recursively
                # Split by semicolons for multiple statements
                statements = [s.strip() for s in body.split(";") if s.strip()]
                for stmt in statements:
                    # Skip 'return' keyword for validation
                    if stmt.startswith("return "):
                        stmt = stmt[7:].strip()
                    # Recursively validate each statement
                    if stmt and not self._validate_directive_expression(stmt):
                        return False
            # Arrow function passed validation
            return True

        # Normalize JavaScript operators to Python equivalents for AST validation
        normalized_expr = expr_stripped

        # Handle JavaScript equality operators
        # === → ==
        # !== → !=
        normalized_expr = normalized_expr.replace("===", "==")
        normalized_expr = normalized_expr.replace("!==", "!=")

        # Handle JavaScript increment/decrement operators
        # counter++ → counter += 1
        # counter-- → counter -= 1
        # ++counter → counter += 1
        # --counter → counter -= 1
        normalized_expr = re.sub(r"(\w+)\+\+", r"\1 += 1", normalized_expr)
        normalized_expr = re.sub(r"(\w+)--", r"\1 -= 1", normalized_expr)
        normalized_expr = re.sub(r"\+\+(\w+)", r"\1 += 1", normalized_expr)
        normalized_expr = re.sub(r"--(\w+)", r"\1 -= 1", normalized_expr)

        # Whitelist of allowed AST node types
        ALLOWED_NODES = {
            # Expression and statement wrappers
            ast.Expression,
            ast.Module,
            ast.Expr,
            # Context nodes
            ast.Load,
            ast.Store,
            # Value nodes
            ast.Name,
            ast.Constant,
            ast.Attribute,
            ast.Subscript,
            # Operations
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.BoolOp,
            ast.IfExp,
            ast.List,
            ast.Tuple,
            ast.Dict,
            ast.Call,
            ast.Index,
            ast.Slice,
            # Assignment nodes (for statements like counter = 0)
            ast.Assign,
            ast.AugAssign,
            ast.AnnAssign,
            # Operator nodes (also checked separately in ALLOWED_OPS)
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.And,
            ast.Or,
            ast.Not,
            ast.UAdd,
            ast.USub,
            ast.In,
            ast.NotIn,
            ast.Is,
            ast.IsNot,
        }

        # Whitelist of allowed operators (redundant with above but kept for clarity)
        ALLOWED_OPS = {
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.And,
            ast.Or,
            ast.Not,
            ast.UAdd,
            ast.USub,
            ast.In,
            ast.NotIn,
            ast.Is,
            ast.IsNot,
        }

        # Blacklist of dangerous function names
        FORBIDDEN_NAMES = {
            "eval",
            "exec",
            "compile",
            "__import__",
            "open",
            "file",
            "input",
            "raw_input",
            "execfile",
            "reload",
            "vars",
            "locals",
            "globals",
            "dir",
            "getattr",
            "setattr",
            "delattr",
            "hasattr",
            "__builtins__",
            "__dict__",
            "__class__",
            "__bases__",
            "__subclasses__",
        }

        # Try AST validation for simple Python-like expressions
        # For complex JavaScript (object literals, multiple statements), skip AST validation
        # since dangerous patterns were already checked above
        try:
            # Try parsing as an expression first
            try:
                tree = ast.parse(normalized_expr, mode="eval")
            except SyntaxError:
                # If it fails, try as a statement (e.g., assignment)
                try:
                    tree = ast.parse(normalized_expr, mode="exec")
                except SyntaxError:
                    # JavaScript syntax that doesn't parse as Python
                    # (object literals, complex expressions, etc.)
                    # Already checked for dangerous patterns above, so allow it
                    return True

            # Walk the AST and validate each node
            for node in ast.walk(tree):
                # Check if node type is allowed
                node_type = type(node)
                if node_type not in ALLOWED_NODES:
                    return False

                # Check operators
                if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp)):
                    if hasattr(node, "op"):
                        if type(node.op) not in ALLOWED_OPS:
                            return False
                    if hasattr(node, "ops"):
                        for op in node.ops:
                            if type(op) not in ALLOWED_OPS:
                                return False

                # Check function calls - block dangerous functions
                # JavaScript methods (push, filter, etc.) won't have ast.Name func
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                        # Block dangerous function calls (already in FORBIDDEN_NAMES but double-check)
                        DANGEROUS_FUNCTIONS = {
                            "eval",
                            "exec",
                            "compile",
                            "__import__",
                            "open",
                            "file",
                            "input",
                            "raw_input",
                            "execfile",
                            "reload",
                            "vars",
                            "locals",
                            "globals",
                            "dir",
                            "getattr",
                            "setattr",
                            "delattr",
                            "hasattr",
                            "Function",  # JavaScript Function constructor
                            "setTimeout",
                            "setInterval",  # Can execute code from strings
                            "alert",  # Can be used for XSS probing
                        }
                        if func_name in DANGEROUS_FUNCTIONS:
                            return False
                        # Allow everything else (fetch, alert, console, etc.)
                        # Dangerous patterns like window.fetch, XHR are already blocked above

                # Check name access - forbid dangerous names
                if isinstance(node, ast.Name):
                    if node.id in FORBIDDEN_NAMES:
                        return False

                # Check attribute access - forbid dunder methods
                if isinstance(node, ast.Attribute):
                    if node.attr.startswith("__") and node.attr.endswith("__"):
                        return False

            return True

        except (SyntaxError, ValueError):
            # Unexpected error - be safe and reject
            return False

    def _precompile_directives(self, html: str) -> tuple[str, dict[str, str]]:
        """Pre-compile Asok directives into a hash-based registry for CSP Zero-Eval security.

        Scans the HTML for asok-* attributes, hashes their JS expressions, and
        replaces the attributes with -ref versions.

        SECURITY: Validates all expressions to prevent code injection.
        """
        import hashlib
        import re

        registry = {}

        # Attributes that contain JS expressions/statements
        expr_attrs = {
            "asok-text",
            "asok-html",
            "asok-show",
            "asok-hide",
            "asok-if",
            "asok-elif",
            "asok-state",
            "asok-init",
            "asok-fetch-async",
        }
        # Note: asok-model is NOT compiled - it's just a property name, not executable code
        # Prefixes for dynamic attributes
        prefixes = ["asok-on:", "asok-class:", "asok-bind:"]

        def get_hash(expr: str) -> str:
            return hashlib.md5(expr.strip().encode()).hexdigest()[:12]

        def replacer(match):
            name = match.group(1)
            val = _html.unescape(match.group(3))

            # Skip if already a ref
            if name.endswith("-ref"):
                return match.group(0)

            # Special case: asok-for="item in items"
            if name == "asok-for":
                if " in " in val:
                    var_part, expr_part = val.split(" in ", 1)
                    # SECURITY: Validate the expression
                    if not self._validate_directive_expression(expr_part):
                        raise ValueError(
                            f"SECURITY: Unsafe expression in {name}: '{expr_part}'. "
                            f"Only safe Python expressions are allowed in directives."
                        )
                    h = get_hash(expr_part)
                    registry[h] = expr_part
                    return f'asok-for-ref="{h}" asok-for-var="{var_part.strip()}"'
                return match.group(0)

            is_expr = name in expr_attrs
            if not is_expr:
                if name == "asok-class":
                    is_expr = True
                else:
                    for p in prefixes:
                        if name.startswith(p):
                            is_expr = True
                            break

            if is_expr:
                # SECURITY: Validate expression before adding to registry
                if not self._validate_directive_expression(val):
                    raise ValueError(
                        f"SECURITY: Unsafe expression in {name}: '{val}'. "
                        f"Only safe Python expressions are allowed in directives. "
                        f"Forbidden: eval(), exec(), __import__(), dunder methods, etc."
                    )
                h = get_hash(val)
                registry[h] = val
                # Handle attributes with colons: asok-on:click -> asok-on-ref:click
                if ":" in name:
                    parts = name.split(":", 1)
                    return f'{parts[0]}-ref:{parts[1]}="{h}"'
                return f'{name}-ref="{h}"'

            return match.group(0)

        # Regex to match asok-*="value" or asok-*='value'
        # We use a non-greedy match for the value to avoid capturing multiple attributes.
        # SECURITY: Use negative lookbehind to avoid matching 'data-asok-*' which contains signed state.
        # re.DOTALL allows . to match newlines for multi-line attribute values
        # Accept dots for event modifiers: asok-on:submit.prevent
        new_html = re.sub(
            r'(?<![a-zA-Z0-9-])(asok-[a-zA-Z0-9:.]+)=([\'"])(.*?)\2',
            replacer,
            html,
            flags=re.DOTALL,
        )

        return new_html, registry

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

        # Robust nonce recovery: if empty, None, or too short, regenerate and sync
        if not nonce or not isinstance(nonce, str) or len(nonce) < 10:
            nonce = request.nonce

        # Ensure the request object and headers will use THIS nonce
        request._nonce = nonce

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

                # Inject page-id marker ONLY for streaming (to indicate where to inject stream content)
                if stream:
                    marker = f"<!-- page-id:{page_id} -->\n"
                    if "</body>" in content.lower():

                        def inject_marker(m):
                            return marker + m.group(1)

                        content = re.sub(
                            r"(</body>)", inject_marker, content, flags=re.I, count=1
                        )
                        request._asok_page_id_done = True
                    else:
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
                        if not self.config.get("DEBUG") and not self.config.get(
                            "ASOK_BUILD"
                        ):
                            scoped_css_content = minify_css(scoped_css_content)
                        # SECURITY: Escape page_id and prevent CSS from breaking </style> tag
                        safe_page_id = _html.escape(page_id, quote=True)
                        safe_css = scoped_css_content.replace("</style>", "<\\/style>")
                        style_tag = f'\n<style id="asok-scoped-css" data-page-id="{safe_page_id}">\n{safe_css}\n</style>\n'

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
                        if not self.config.get("DEBUG") and not self.config.get(
                            "ASOK_BUILD"
                        ):
                            scoped_js_content = minify_js(scoped_js_content)
                        # SECURITY: Prevent JS from breaking </script> tag
                        safe_js = scoped_js_content.replace("</script>", "<\\/script>")
                        request._asok_pending_scripts += (
                            f'\n<script id="asok-scoped-js" nonce="{nonce}">'
                            "(function(){"
                            "const init=function(){" + safe_js + "};"
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
            # Shared transition styles - inject in styles
            if not hasattr(request, "_asok_pending_styles"):
                request._asok_pending_styles = ""

            request._asok_pending_styles += (
                f'<style id="asok-transitions" nonce="{nonce}">'
                # Custom easing functions (SvelteKit-like)
                ":root {"
                "--ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);"
                "--ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);"
                "--ease-in-out-back: cubic-bezier(0.68, -0.6, 0.32, 1.6);"
                "}"
                ".asok-transitioning { position: relative; overflow: hidden; pointer-events: none; }"
                # Fade transition (improved)
                ".asok-fade-out { opacity: 1; }"
                ".asok-fade-out.is-leaving { opacity: 0; transition: opacity 300ms ease-out; }"
                ".asok-fade-in { opacity: 0; }"
                ".asok-fade-in.is-entering { opacity: 1; transition: opacity 300ms ease-out; }"
                # Slide transition (improved with expo easing)
                ".asok-slide-out { transform: translateX(0); opacity: 1; }"
                ".asok-slide-out.is-leaving { transform: translateX(-20px); opacity: 0; transition: all 300ms var(--ease-out-expo); }"
                ".asok-slide-in { transform: translateX(20px); opacity: 0; }"
                ".asok-slide-in.is-entering { transform: translateX(0); opacity: 1; transition: all 300ms var(--ease-out-expo); }"
                # Scale transition (improved with quart easing)
                ".asok-scale-out { transform: scale(1); opacity: 1; }"
                ".asok-scale-out.is-leaving { transform: scale(0.95); opacity: 0; transition: all 250ms var(--ease-out-quart); }"
                ".asok-scale-in { transform: scale(0.95); opacity: 0; }"
                ".asok-scale-in.is-entering { transform: scale(1); opacity: 1; transition: all 300ms var(--ease-out-quart); }"
                # Fly transition (NEW - like SvelteKit)
                ".asok-fly-out { transform: translateY(0); opacity: 1; }"
                ".asok-fly-out.is-leaving { transform: translateY(-20px); opacity: 0; transition: all 300ms var(--ease-out-expo); }"
                ".asok-fly-in { transform: translateY(20px); opacity: 0; }"
                ".asok-fly-in.is-entering { transform: translateY(0); opacity: 1; transition: all 300ms var(--ease-out-expo); }"
                # Blur transition (NEW - subtle and modern)
                ".asok-blur-out { filter: blur(0px); opacity: 1; }"
                ".asok-blur-out.is-leaving { filter: blur(5px); opacity: 0; transition: all 200ms ease-out; }"
                ".asok-blur-in { filter: blur(5px); opacity: 0; }"
                ".asok-blur-in.is-entering { filter: blur(0px); opacity: 1; transition: all 300ms ease-out; }"
                # Bounce transition (NEW - elastic effect)
                ".asok-bounce-out { transform: scale(1); opacity: 1; }"
                ".asok-bounce-out.is-leaving { transform: scale(0.9); opacity: 0; transition: all 300ms ease-in; }"
                ".asok-bounce-in { transform: scale(0.9); opacity: 0; }"
                ".asok-bounce-in.is-entering { transform: scale(1); opacity: 1; transition: all 400ms var(--ease-in-out-back); }"
                # Page transition (for SPA navigation)
                ".asok-page-out { opacity: 1; transform: scale(1); }"
                ".asok-page-out.is-leaving { opacity: 0; transform: scale(0.98); transition: all 250ms var(--ease-out-quart); }"
                ".asok-page-in { opacity: 0; transform: scale(0.98); }"
                ".asok-page-in.is-entering { opacity: 1; transform: scale(1); transition: all 300ms var(--ease-out-quart); }"
                "</style>\n"
            )

            request._asok_pending_scripts += (
                f'<script id="asok-transition-engine" nonce="{nonce}">'
                "(function(){"
                "window.Asok=window.Asok||{};"
                "window.Asok.swap=function(t,h,mode,callback){"
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
                "setTimeout(()=>{raw(t,h,mode);if(callback)callback();t.classList.remove('asok-'+type+'-out','is-leaving');"
                "t.classList.add('asok-'+type+'-in');"
                "requestAnimationFrame(()=>{t.classList.add('is-entering');"
                "setTimeout(()=>{t.classList.remove('asok-'+type+'-in','is-entering')},dur)});"
                "},dur)}else{raw(t,h,mode);if(callback)callback()}"
                "    };\n"
                "})()\n"
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
                f'<script nonce="{nonce}">\n'
                ";(function(){window.Asok=window.Asok||{};"
                # SECURITY: Limit SPA cache size to prevent memory DoS
                "const ca={},cak=[],MAX_CACHE=100;"
                "window.__asokClearCache=function(){Object.keys(ca).forEach(k=>delete ca[k]);cak.length=0};"
                "function addCache(k,v){if(cak.length>=MAX_CACHE){const old=cak.shift();delete ca[old]}ca[k]=v;cak.push(k)}"
                "function ct(){const m=document.querySelector('meta[name=csrf-token]');return m?m.content:''}"
                "function qb(s){if(!s)return null;let t;try{t=document.querySelector(s)}catch(e){}"
                "if(!t&&/^[a-zA-Z0-9_-]+$/.test(s))t=document.getElementById(s);"
                "if(!t&&s==='title')t=document.querySelector('title');"
                "if(!t&&s==='description')t=document.querySelector('meta[name=description]');"
                "if(!t&&/^[a-zA-Z0-9_-]+$/.test(s)){"
                "const it=document.createNodeIterator(document.body,NodeFilter.SHOW_COMMENT);"
                "let c;while(c=it.nextNode()){if(c.textContent.trim()==='block:'+s+':start'){"
                "t={_isBlockMarker:true,_blockName:s,_startMarker:c};break}}}return t}"
                "function doSwap(t,h,mode,pushData){"
                "  const realTarget=t._isBlockMarker?t._startMarker.parentNode:t;"
                # BUGFIX: Cleanup before swap for WebSocket components
                "  const beforeSwap=function(){"
                "    realTarget.querySelectorAll('[data-asok-component]').forEach(function(el){"
                # Clear all init flags so component can be fully reinited after swap
                "      delete el.__asokWsReady;"
                "      delete el.__asokIniting;"
                # Notify server of component removal
                "      if(window.Asok&&window.Asok.leaveComponent){"
                "        window.Asok.leaveComponent(el.id.replace('asok-',''));"
                "      }"
                "    });"
                "  };"
                "  const afterSwap=function(){"
                "    if(window.AsokDirectives && window.AsokDirectives.forceInit) window.AsokDirectives.forceInit(realTarget);"
                "    if(window.Asok && window.Asok.init) window.Asok.init(realTarget);"
                "    if(window.lucide && window.lucide.createIcons) window.lucide.createIcons();"
                "    if(pushData && pushData.shouldPush){"
                "        const ov=document.getElementById('search-overlay'); if(ov) ov.classList.remove('open');"
                "        const mm=document.getElementById('mobile-menu'); if(mm) mm.classList.add('hidden');"
                "        document.body.style.overflow='';"
                "        if(pushData.src && pushData.src.dataset && pushData.src.dataset.pushUrl !== undefined){"
                "            const pu = pushData.src.dataset.pushUrl || pushData.url;"
                "            history.pushState({b:pushData.b, sel:pushData.sel, mode:mode, url:pushData.url}, '', pu);"
                "        }"
                "        window.scrollTo({top: 0, behavior: 'instant'});"
                # Apply page-level transition if configured
                "        const pageContainer = document.querySelector('[data-asok-page-transition]');"
                "        if(pageContainer){"
                "          const ptr = pageContainer.getAttribute('data-asok-page-transition') || 'page';"
                "          const parts = ptr.split(' ');"
                "          const type = parts[0];"
                "          const dur = parseInt(parts[1]) || 300;"
                "          pageContainer.classList.add('asok-'+type+'-in');"
                "          requestAnimationFrame(()=>{"
                "            pageContainer.classList.add('is-entering');"
                "            setTimeout(()=>pageContainer.classList.remove('asok-'+type+'-in','is-entering'),dur);"
                "          });"
                "        }"
                "    }"
                "    const ev = new CustomEvent('asok:success', {detail:{target:realTarget, mode:mode}});"
                "    document.dispatchEvent(ev);"
                "  };"
                "  if(t._isBlockMarker){"
                "    beforeSwap();"
                "    const start=t._startMarker,name=t._blockName;"
                "    const it=document.createNodeIterator(document.body,NodeFilter.SHOW_COMMENT);"
                "    let c,end=null;while(c=it.nextNode()){if(c===start){while(c=it.nextNode()){"
                "    if(c.textContent.trim()==='block:'+name+':end'){end=c;break}}break}}"
                "    if(!end)return;"
                "    const nodes=[];let n=start.nextSibling;while(n&&n!==end){nodes.push(n);n=n.nextSibling}"
                "    nodes.forEach(function(x){x.remove()});"
                "    const tmp=document.createElement('div');tmp.innerHTML=h;"
                "    Array.from(tmp.childNodes).forEach(function(x){start.parentNode.insertBefore(x,end)});"
                "    afterSwap();"
                "  }else if(t.tagName==='META'){"
                "    t.content=h;afterSwap();"
                "  }else{"
                "    beforeSwap();"
                "    if(window.Asok&&window.Asok.swap){window.Asok.swap(t,h,mode,afterSwap)}else{t.innerHTML=h;afterSwap()}"
                "    if(t.tagName==='TITLE')document.title=t.innerText;"
                "  }"
                "}"
                "function sw(url,b,sel,mode,opts,src){"
                "if(document.dispatchEvent(new CustomEvent('asok:before', {detail:{url:url, block:b}})) === false) return;"
                "const h = Object.assign({'X-Block':b, 'X-CSRF-Token':ct()}, opts.headers || {});"
                "opts.headers = h; opts.credentials = 'same-origin';"
                "const key = url + b;"
                "const p = ca[key] ? Promise.resolve(ca[key]) : fetch(url, opts).then(function(r){"
                "if(!r.ok){ return r.text().then(function(t){"
                "  const ev = new CustomEvent('asok:error', {detail:{url:url, status:r.status, message:t}});"
                "  document.dispatchEvent(ev);"
                "  console.error((r.status === 400 ? 'Asok Consistency Error: ' : 'Asok Error ' + r.status + ': ') + t);"
                "  throw t;"
                "}); }"
                "const redir=r.headers.get('X-Asok-Redirect');"
                "if(redir){window.location.href=redir;return Promise.reject('r')}"
                "const c=r.headers.get('X-CSRF-Token'),bks=r.headers.get('X-Asok-Blocks');"
                "if(c){const m=document.querySelector('meta[name=csrf-token]');if(m)m.content=c;"
                "document.querySelectorAll('input[name=csrf_token]').forEach(function(i){i.value=c})}"
                "if(bks)window.Asok.lastBlocks=bks;"
                "const sqlLog=r.headers.get('X-Asok-SQL-Log');if(sqlLog){window.Asok.lastSqlLog=sqlLog;}else{window.Asok.lastSqlLog=null;}"
                "return r.text()});"
                "delete ca[key];"
                "return p.then(function(h){"
                "if(!h)return;"
                "const tr=h.trimStart();"
                "if(tr.startsWith('<!DOCTYPE')||tr.startsWith('<html')){"
                "window.location.href=url;return}"
                "const d=document.createElement('div');d.innerHTML=h;"
                "const tpls=d.querySelectorAll('template[data-block]');"
                "const isP=(src&&src.dataset&&src.dataset.pushUrl!==undefined)||(!src&&url);"
                "const pushData=isP?{shouldPush:true,src:src,url:url,b:b,sel:sel}:null;"
                "if(tpls.length){"
                "for(let i=0;i<tpls.length;i++){"
                "const tpl=tpls[i];"
                "const t=qb(tpl.dataset.block);"
                "if(t)doSwap(t,tpl.innerHTML,tpl.dataset.swap||'innerHTML',i===tpls.length-1?pushData:null)}"
                "}else{"
                "const t=qb(sel);"
                "if(t)doSwap(t,h,mode,pushData)}"
                "const get=function(s){let e=d.querySelector(s);if(!e){const ts=d.querySelectorAll('template');for(let i=0;i<ts.length;i++){e=ts[i].content.querySelector(s);if(e)break}}return e};"
                "const scs=get('#asok-scoped-css');const oldCss=document.getElementById('asok-scoped-css');if(scs){if(oldCss)oldCss.remove();document.head.appendChild(scs)}else if(oldCss&&isP){oldCss.remove()}"
                "const scj=get('#asok-scoped-js');const oldJs=document.getElementById('asok-scoped-js');if(scj){if(oldJs)oldJs.remove();const ns=document.createElement('script');ns.id='asok-scoped-js';if(scj.nonce)ns.nonce=scj.nonce;ns.textContent=scj.textContent;document.body.appendChild(ns)}else if(oldJs&&isP){oldJs.remove()}"
                "const findPageId=function(){const it=d.createNodeIterator(d.body,NodeFilter.SHOW_COMMENT);let c;while(c=it.nextNode()){const m=c.textContent.match(/^\\s*page-id:(.+)$/);if(m)return m[1].trim()}return null};"
                "const pid=findPageId();if(pid){document.body.dataset.pageId=pid}else if(isP){delete document.body.dataset.pageId}"
                "},function(){})"
                "}"
                "function pf(u,b){if(ca[u+b]||!u||!b)return;fetch(u,{headers:{'X-Block':b,'X-Prefetch':'1'},credentials:'same-origin'}).then(function(r){if(r.ok)r.text().then(function(t){addCache(u+b,t)})})}"
                "function resolve(el){"
                "const f=el.tagName==='FORM'?el:el.closest('form');"
                "const b=el.dataset.block||(f?f.dataset.block:null);if(!b)return null;"
                "const sel=el.dataset.target||b.split(',')[0];"
                "const swap=el.dataset.swap||'innerHTML';"
                "let url,method,body=null;"
                "const da=el.dataset.action||(f?f.dataset.action:null);"
                "if(f && (el===f || el.type==='submit' || el.dataset.action)){"
                "url=f.action||location.pathname;"
                "method=(f.method||'POST').toUpperCase();"
                "const fd=new FormData(f);if(da)fd.append('_action',da);"
                "if(el.name && el!==f)fd.append(el.name,el.value);"
                "if(method === 'GET'){"
                "  const p = new URLSearchParams(fd).toString();"
                "  if(p) url += (url.indexOf('?') < 0 ? '?' : '&') + p;"
                "} else { body = fd; }"
                "}else if(el.tagName==='A'){"
                "url=el.href;method='GET';"
                "if(da)url+=(url.indexOf('?')<0?'?':'&')+'_action='+da"
                "}else{"
                "url=el.dataset.url||location.pathname;"
                "method=(el.dataset.method||(da?'POST':'GET')).toUpperCase();"
                "const fd=new FormData();"
                "if(el.name)fd.append(el.name,el.value||'');"
                "if(da)fd.append('_action',da);"
                "if(method==='GET'){"
                "const p=new URLSearchParams(fd).toString();"
                "if(p)url+=(url.indexOf('?')<0?'?':'&')+p"
                "}else body=fd"
                "}"
                "const inc = el.dataset.include;"
                "if(inc){"
                "const extras = document.querySelectorAll(inc);"
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
                # Apply page-level transition OUT if it's a push navigation
                "const isPageNav=(el.dataset&&el.dataset.pushUrl!==undefined)||el.tagName==='A';"
                "const pageContainer=document.querySelector('[data-asok-page-transition]');"
                "if(isPageNav&&pageContainer){"
                "  const ptr=pageContainer.getAttribute('data-asok-page-transition')||'page';"
                "  const parts=ptr.split(' ');"
                "  const type=parts[0];"
                "  const dur=parseInt(parts[1])||250;"
                "  pageContainer.classList.add('asok-'+type+'-out');"
                "  requestAnimationFrame(()=>pageContainer.classList.add('is-leaving'));"
                "  setTimeout(()=>pageContainer.classList.remove('asok-'+type+'-out','is-leaving'),dur);"
                "}"
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
                # CRITICAL: Ignore clicks inside WebSocket components (they handle their own events)
                "if(e.target.closest('[data-asok-component]'))return;"
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
                "es.onmessage = function(ev){"
                "  const d = document.createElement('div'); d.innerHTML = ev.data;"
                "  const tpls = d.querySelectorAll('template[data-block]');"
                "if(tpls.length){"
                "for(let i=0;i<tpls.length;i++){"
                "const tpl=tpls[i];"
                "const t=qb(tpl.dataset.block);"
                "    if(t) doSwap(t, tpl.innerHTML, tpl.dataset.swap || 'innerHTML', null);"
                "  } "
                "} "
                "else {"
                "  const t = qb(sel);"
                "  if(t) doSwap(t, ev.data, mode, null);"
                "}"
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
                "else fire(el);"
                "});"
                "});"
                "}"
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);"
                "else setup();"
                "})(); /* Asok Reactive Engine v1.0.1 */\n"
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
                f'<script nonce="{nonce}">\n'
                "window.asokWS = function(path) {\n"
                "  const p = (location.protocol === 'https:') ? 'wss:' : 'ws:';\n"
                f"  let h = location.hostname + ':{ws_port}';\n"
                "  if (location.hostname !== 'localhost') {\n"
                "    if (location.hostname !== '127.0.0.1') {\n"
                "      if (location.hostname !== '0.0.0.0') {\n"
                "        if (!location.hostname.startsWith('192.168.')) {\n"
                "           h = location.host + '/ws';\n"
                "        }\n"
                "      }\n"
                "    }\n"
                "  }\n"
                "  return new WebSocket(p + '/' + '/' + h + path);\n"
                "};\n"
                "</script>\n"
            )

            # Alive Engine
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n'
                "(function() {\n"
                "  let ws;\n"
                "  const timers = {};\n"
                "  let connecting = false;\n"
                "  function connect() {\n"
                "    if (connecting) return;\n"
                "    if (ws) {\n"
                "      if (ws.readyState === 0) return;\n"
                "      if (ws.readyState === 1) ws.close();\n"
                "    }\n"
                "    connecting = true;\n"
                "    ws = window.asokWS('/asok/live');\n"
                "    ws.onopen = function() {\n"
                "      connecting = false;\n"
                "      if (window._asokPendingInits && window._asokPendingInits.length) {\n"
                "        const pending = window._asokPendingInits.slice();\n"
                "        window._asokPendingInits = [];\n"
                "        pending.forEach(function(el) {\n"
                "          if (document.body.contains(el)) {\n"
                "            delete el.__asokIniting;\n"
                "            delete el.__asokWsReady;\n"
                "            window.Asok._wsInit(el);\n"
                "          }\n"
                "        });\n"
                "      }\n"
                "      document.querySelectorAll('[data-asok-component]').forEach(window.Asok._wsInit);\n"
                "      document.querySelectorAll('[data-subscribe]').forEach(window.Asok._wsSub);\n"
                "    };\n"
                "    ws.onmessage = function(e) {\n"
                "      const d = JSON.parse(e.data);\n"
                "      if (d.op === 'render') {\n"
                "        const el = document.getElementById('asok-' + d.cid);\n"
                "        if (el) {\n"
                "          if (d.registry) {\n"
                "            let code = '';\n"
                "            for (let h in d.registry) {\n"
                "              code += 'window.__asok_registry[' + JSON.stringify(h) + '] = (' + d.registry[h] + ');\\n';\n"
                "            }\n"
                "            const s = document.createElement('script');\n"
                "            s.nonce = window.Asok.nonce;\n"
                "            s.textContent = code;\n"
                "            document.head.appendChild(s);\n"
                "            s.remove();\n"
                "          }\n"
                "          if (d.invalidate_cache) {\n"
                "            if (window.__asokClearCache) window.__asokClearCache();\n"
                "          }\n"
                "          const newEl = new DOMParser().parseFromString(d.html, 'text/html').body.firstElementChild;\n"
                "          el.replaceWith(newEl);\n"
                "          const updated = document.getElementById('asok-' + d.cid);\n"
                "          if (updated) {\n"
                "            if (window.AsokDirectives && window.AsokDirectives.init) window.AsokDirectives.init(updated);\n"
                "            initWS(updated, true);\n"
                "            document.dispatchEvent(new CustomEvent('asok:ws-update', {detail: {cid: d.cid, name: d.name, state: d.state}}));\n"
                "          }\n"
                "        }\n"
                "      } else if (d.op === 'model_event') {\n"
                "        document.querySelectorAll('[data-subscribe]').forEach(function(el) {\n"
                "          const room = el.dataset.subscribe;\n"
                "          if (room === 'model:' + d.model || room === 'model:' + d.model + ':' + d.id) {\n"
                "            if (window.Asok && window.Asok.refresh) window.Asok.refresh(el);\n"
                "            else if (typeof fire === 'function') fire(el);\n"
                "          }\n"
                "        });\n"
                "      } else if (d.op === 'broadcast') {\n"
                "        document.dispatchEvent(new CustomEvent('asok:ws-broadcast', {detail: d}));\n"
                "      }\n"
                "    };\n"
                "    ws.onclose = function() {\n"
                "      connecting = false;\n"
                "      setTimeout(connect, 2000);\n"
                "    };\n"
                "    ws.onerror = function() {\n"
                "      connecting = false;\n"
                "    };\n"
                "  }\n"
                "  function send(msg, el) {\n"
                "    if (!ws || ws.readyState !== 1) return;\n"
                "    if (el) el.classList.add('asok-loading');\n"
                "    ws.send(JSON.stringify(msg));\n"
                "  }\n"
                "  function initSub(el) {\n"
                "    if (el.__asokSubReady) return;\n"
                "    el.__asokSubReady = true;\n"
                "    send({op: 'join_room', room: el.dataset.subscribe});\n"
                "  }\n"
                "  function initWS(el, skipJoin) {\n"
                "    if (el.__asokIniting) return;\n"
                "    el.__asokIniting = true;\n"
                "    const cid = el.id.replace('asok-', '');\n"
                "    const base = el.dataset.asokComponent;\n"
                "    const st = el.dataset.asokState;\n"
                "    if (!ws || ws.readyState !== 1) {\n"
                "      if (!window._asokPendingInits) window._asokPendingInits = [];\n"
                "      window._asokPendingInits.push(el);\n"
                "      delete el.__asokIniting;\n"
                "      return;\n"
                "    }\n"
                "    if (!skipJoin) {\n"
                "      send({op: 'join', cid: cid, name: base, state: st});\n"
                "    }\n"
                "    ['click', 'input', 'change', 'submit', 'keyup', 'keydown'].forEach(function(ev) {\n"
                "      el.querySelectorAll('[ws-' + ev + ']').forEach(function(n) {\n"
                "        const attr = n.getAttribute('ws-' + ev);\n"
                "        const parts = attr.split('.');\n"
                "        const meth = parts[0];\n"
                "        const mods = parts.slice(1);\n"
                "        const handler = function(e) {\n"
                "          if (mods.includes('prevent')) e.preventDefault();\n"
                "          if (mods.includes('stop')) e.stopPropagation();\n"
                "          if (mods.includes('enter')) {\n"
                "            if (e.key !== 'Enter') return;\n"
                "          }\n"
                "          const val = n.value;\n"
                "          const msg = {op: 'call', cid: cid, method: meth, val: val};\n"
                "          const deb = mods.find(function(m) { return m.startsWith('debounce'); });\n"
                "          if (deb) {\n"
                "            const ms = parseInt(deb.split('-')[1]) || 300;\n"
                "            clearTimeout(timers[n]);\n"
                "            timers[n] = setTimeout(function() { send(msg, n); }, ms);\n"
                "          } else {\n"
                "            send(msg, n);\n"
                "          }\n"
                "        };\n"
                "        n['on' + ev] = handler;\n"
                "      });\n"
                "    });\n"
                "    el.querySelectorAll('[ws-model]').forEach(function(n) {\n"
                "      const prop = n.getAttribute('ws-model');\n"
                "      n.oninput = function() {\n"
                "        send({op: 'sync', cid: cid, prop: prop, val: n.value}, n);\n"
                "      };\n"
                "    });\n"
                "    el.__asokWsReady = true;\n"
                "    delete el.__asokIniting;\n"
                "  }\n"
                "  window.Asok = window.Asok || {};\n"
                "  window.Asok._wsInit = initWS;\n"
                "  window.Asok._wsSub = initSub;\n"
                "  document.addEventListener('asok:success', function(e) {\n"
                "    if (e.detail && e.detail.target) {\n"
                "      const el = e.detail.target;\n"
                "      if (el.dataset.asokComponent) initWS(el);\n"
                "      if (el.dataset.subscribe) initSub(el);\n"
                "      el.querySelectorAll('[data-asok-component]').forEach(initWS);\n"
                "      el.querySelectorAll('[data-subscribe]').forEach(initSub);\n"
                "    }\n"
                "  });\n"
                "  if (document.readyState === 'loading') {\n"
                "    document.addEventListener('DOMContentLoaded', connect);\n"
                "  } else {\n"
                "    connect();\n"
                "  }\n"
                "})();\n"
                "</script>\n"
            )

        # 2. Inject nonce into all existing <script>, <style>, and <link> tags to satisfy strict-dynamic CSP
        def inject_nonce_attr(m):
            tag = m.group(1)
            attrs = m.group(2)
            # If nonce attribute already exists (placeholder or old value), replace it
            if 'nonce="' in attrs.lower():
                return re.sub(r'(?i)nonce=".*?"', f'nonce="{nonce}"', m.group(0))
            # Otherwise append it
            return f'<{tag}{attrs} nonce="{nonce}">'

        content = re.sub(
            r"<(script|style|link)\b([^>]*?)>",
            inject_nonce_attr,
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
                    "asok-fetch",
                    "asok-fetch-async",
                ]
            )
            # CRITICAL: Always precompile directives, even for blocks!
            # SPA navigation needs the registry to be updated with new expressions
        )
        if needs_directives:
            # 3.1 Pre-compile directives for Zero-Eval Security
            content, registry = self._precompile_directives(content)

            # ALWAYS generate and inject the registry if there are new directives
            # Even if the runtime has already been loaded (for SPA navigation)
            registry_js = ""
            if registry:
                registry_entries = []
                for h, expr in registry.items():
                    # Determine if it's a statement or an expression for the 'return' keyword
                    is_stmt = ";" in expr or "if " in expr or "return " in expr
                    # Special case for asok-state: it's always an expression returning an object
                    if expr.strip().startswith("{") and not is_stmt:
                        expr = f"({expr})"

                    body = f"return ({expr})" if not is_stmt else expr
                    # Simple minification: collapse whitespace only
                    # NOTE: If you use // comments in directives, they will break inline injection
                    body = re.sub(r"\s+", " ", body).strip()

                    registry_entries.append(
                        f"    {json.dumps(h)}: function($, $store, $el, $event, $refs, $nextTick) {{ with($||{{}}) {{ {body} }} }}"
                    )
                registry_js = (
                    "window.__asok_registry = Object.assign(window.__asok_registry || {}, {\n"
                    + ",\n".join(registry_entries)
                    + "\n});\n"
                )

                # For SPA blocks (runtime already loaded), inject JUST the registry
                if getattr(request, "_asok_directives_done", False):
                    request._asok_pending_scripts += (
                        f'<script nonce="{nonce}">\n{registry_js}</script>\n'
                    )
                # For initial page load, registry will be injected with the runtime below

            # Only inject the full runtime (styles + directives engine) once
            if not getattr(request, "_asok_directives_done", False):
                request._asok_directives_done = True

            request._asok_pending_styles += (
                f'<style nonce="{nonce}">'
                "[asok-cloak]{display:none!important}"
                ".asok-dropdown{position:relative;width:100%}"
                ".asok-dropdown-trigger{display:flex;align-items:center;justify-content:space-between;width:100%;padding:0.75rem 1rem;border:1px solid var(--asok-border,#ccc);border-radius:8px;background:var(--asok-input-bg,#fff);cursor:pointer;font-family:inherit;font-size:1rem;text-align:left}"
                ".asok-dropdown-arrow{transition:transform 0.2s;margin-left:auto}"
                ".asok-dropdown-menu{position:absolute;top:100%;left:0;right:0;z-index:50;margin-top:0.5rem;background:var(--asok-dropdown-bg,rgba(255,255,255,0.95));border:1px solid var(--asok-border,#ccc);border-radius:8px;box-shadow:0 10px 15px -3px rgba(0,0,0,0.1);overflow:hidden;backdrop-filter:blur(8px)}"
                ".asok-dropdown-search{padding:0.5rem;border-bottom:1px solid var(--asok-border,#eee)}"
                ".asok-dropdown-search input{width:100%;padding:0.5rem;border:none;outline:none;background:transparent;font-size:0.9rem}"
                ".asok-dropdown-items{max-height:250px;overflow-y:auto}"
                ".asok-dropdown-item{display:flex;align-items:center;padding:0.75rem 1rem;cursor:pointer;transition:background 0.2s}"
                ".asok-dropdown-item:hover{background:var(--asok-hover,#f3f4f6)}"
                ".asok-dropdown-item-img{width:32px;height:32px;border-radius:50%;margin-right:0.75rem;object-fit:cover}"
                ".asok-dropdown-item-title{font-weight:500;color:var(--asok-text,#111)}"
                ".asok-dropdown-item-subtitle{font-size:0.8rem;color:var(--asok-text-muted,#666)}"
                ".asok-table-container{width:100%;background:var(--asok-table-bg,#fff);border:1px solid var(--asok-border,#eee);border-radius:12px;overflow:hidden;box-shadow:0 4px 6px -1px rgba(0,0,0,0.1)}"
                ".asok-table-header{display:flex;align-items:center;justify-content:space-between;padding:1rem;background:var(--asok-table-header-bg,#f9fafb);border-bottom:1px solid var(--asok-border,#eee);gap:1rem;flex-wrap:wrap}"
                ".asok-table-wrapper{width:100%;overflow-x:auto}"
                ".asok-table{width:100%;border-collapse:collapse;text-align:left;font-size:0.95rem}"
                ".asok-table th{padding:0.75rem 1rem;background:var(--asok-table-header-bg,#f9fafb);font-weight:600;color:var(--asok-text-muted,#4b5563);text-transform:uppercase;font-size:0.75rem;letter-spacing:0.05em}"
                ".asok-table td{padding:1rem;border-bottom:1px solid var(--asok-border,#eee);color:var(--asok-text,#1f2937)}"
                ".asok-table tr:hover{background:var(--asok-hover,#f3f4f6)}"
                ".asok-table-actions{display:flex;gap:0.5rem;align-items:center}"
                ".asok-btn-table{padding:0.4rem 0.8rem;border-radius:6px;background:var(--asok-accent,#3b82f6);color:#fff;text-decoration:none;font-size:0.85rem;font-weight:500;transition:opacity 0.2s}"
                ".asok-btn-table:hover{opacity:0.9}"
                ".asok-table-footer{display:flex;align-items:center;justify-content:space-between;padding:1rem;background:var(--asok-table-header-bg,#f9fafb);border-top:1px solid var(--asok-border,#eee);font-size:0.85rem;color:var(--asok-text-muted,#6b7280);flex-wrap:wrap;gap:1rem}"
                ".asok-pagination{display:flex;gap:0.25rem}"
                ".asok-page-link{padding:0.4rem 0.75rem;border:1px solid var(--asok-border,#d1d5db);border-radius:6px;background:#fff;color:var(--asok-text,#374151);text-decoration:none;transition:all 0.2s}"
                ".asok-page-link:hover{background:#f3f4f6}"
                ".asok-page-link.active{background:var(--asok-accent,#3b82f6);color:#fff;border-color:var(--asok-accent,#3b82f6)}"
                ".asok-search-input, .asok-filter-select{padding:0.5rem 0.75rem;border:1px solid var(--asok-border,#d1d5db);border-radius:8px;font-size:0.9rem;outline:none;background:#fff}"
                ".asok-search-input:focus, .asok-filter-select:focus{border-color:var(--asok-accent,#3b82f6);box-shadow:0 0 0 3px rgba(59,130,246,0.1)}"
                ".asok-badge{display:inline-flex;padding:0.2rem 0.6rem;border-radius:9999px;font-size:0.75rem;font-weight:600;text-transform:uppercase}"
                ".asok-badge-success{background:#dcfce7;color:#166534}"
                ".asok-badge-danger{background:#fee2e2;color:#991b1b}"
                ".asok-sort-icon{display:inline-block;width:0;height:0;margin-left:5px;vertical-align:middle;border-right:4px solid transparent;border-left:4px solid transparent;transition:all 0.2s}"
                ".asok-sort-asc{border-bottom:4px solid var(--asok-text,#111)}"
                ".asok-sort-desc{border-top:4px solid var(--asok-text,#111)}"
                ".asok-bulk-actions{display:flex;align-items:center;padding:0.5rem 1rem;background:var(--asok-accent-light,#eff6ff);border-radius:8px;border:1px solid var(--asok-accent,#3b82f6);color:var(--asok-accent-dark,#1e40af);font-size:0.9rem}"
                ".asok-btn-bulk{padding:0.3rem 0.7rem;border-radius:6px;font-size:0.8rem;font-weight:600;margin-left:0.5rem;cursor:pointer;border:none;transition:all 0.2s}"
                ".asok-btn-danger{background:#ef4444;color:#fff}"
                ".asok-btn-danger:hover{background:#dc2626}"
                ".asok-row-selected{background:var(--asok-accent-light,#eff6ff)!important}"
                ".asok-table-checkbox{width:40px;text-align:center}"
                ".asok-table-checkbox input{width:18px;height:18px;cursor:pointer}"
                "</style>"
            )
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n'
                f'window.Asok = window.Asok || {{}}; window.Asok.nonce = "{nonce}";\n'
                f"{registry_js}\n"
                ";(function(){const w=new WeakMap(),sd=new Map();let cs=null;"
                "const st=new Proxy({},{get(t,p){if(cs&&!p.startsWith('_')){if(!sd.has(p))sd.set(p,new Set());sd.get(p).add(cs)}return t[p]},set(t,p,v){if(t[p]===v)return true;t[p]=v;if(sd.has(p)){sd.get(p).forEach(el=>{if(!document.body.contains(el)){sd.get(p).delete(el);return}const c=w.get(el);if(c)us(el)})}return true}});"
                "const fss=(el)=>{while(el&&el!==document.documentElement){if(w.has(el))return el;el=el.parentElement}return null};"
                "const gs=(st,el,ev)=>{const sc=fss(el),c=sc?w.get(sc):{refs:{}};const localState=c.state||st;return[localState,window.Asok.store,el,ev,c.refs||{},f=>Promise.resolve().then(f)]};"
                "const se=(ref,st,el)=>{const fn=(window.__asok_registry||{})[ref];if(!fn)return;try{return fn(...gs(st,el))}catch(e){}};"
                "const es=(ref,st,ev,el)=>{const fn=(window.__asok_registry||{})[ref];if(!fn)return;try{return fn(...gs(st,el,ev))}catch(e){}};"
                "const ub=(el,st)=>{if(!el||!st)return;const at=el.getAttribute.bind(el),tra=at('asok-transition'),tr=tra?tra.split(' ').filter(c=>c):[];"
                "if(el.hasAttribute('asok-text-ref')){const v=se(at('asok-text-ref'),st,el);if(v!==undefined)el.textContent=String(v)}"
                "if(el.hasAttribute('asok-html-ref')){const v=se(at('asok-html-ref'),st,el);if(v!==undefined)el.innerHTML=String(v).replace(/<script\\b[^<]*(?:(?!<\\/script>)<[^<]*)*<\\/script>/gi,'')}"
                "if(el.hasAttribute('asok-show-ref')){const v=se(at('asok-show-ref'),st,el);if(v){if(el.style.display==='none')el._st=Date.now();el.style.display='';if(tr.length)el.classList.add(...tr);el.setAttribute('data-show-active','');if(tr.length)el.addEventListener('transitionend',()=>el.classList.remove(...tr),{once:true})}else{if(tr.length){el.classList.add(...tr);el.addEventListener('transitionend',()=>{el.style.display='none';el.classList.remove(...tr)},{once:true})}else el.style.display='none';el.removeAttribute('data-show-active')}}"
                "if(el.hasAttribute('asok-hide-ref')){const v=se(at('asok-hide-ref'),st,el);if(!v){el.style.display='';if(tr.length)el.classList.add(...tr);el.removeAttribute('data-hide-active');if(tr.length)el.addEventListener('transitionend',()=>el.classList.remove(...tr),{once:true})}else{if(tr.length){el.classList.add(...tr);el.addEventListener('transitionend',()=>{el.style.display='none';el.classList.remove(...tr)},{once:true})}else el.style.display='none';el.setAttribute('data-hide-active','')}}"
                "Array.from(el.attributes).forEach(a=>{"
                "if(a.name==='asok-class-ref'){const v=se(a.value,st,el);if(typeof v==='string'){const prev=(el._ac||'').split(' ').filter(c=>c),curr=v.split(' ').filter(c=>c);prev.forEach(c=>{if(!curr.includes(c))el.classList.remove(c)});curr.forEach(c=>el.classList.add(c));el._ac=v}else if(typeof v==='object'&&v){Object.keys(v).forEach(k=>{const cls=k.split(' ').filter(c=>c);cls.forEach(c=>el.classList[v[k]?'add':'remove'](c))})}}"
                "if(a.name.startsWith('asok-class-ref:')){const c=a.name.substring(15);el.classList[se(a.value,st,el)?'add':'remove'](c)}"
                "if(a.name.startsWith('asok-bind-ref:')){const n=a.name.substring(14),v=se(a.value,st,el);if(v!==undefined&&v!==null&&v!==false)el.setAttribute(n,String(v));else el.removeAttribute(n);}"
                "});"
                "};"
                "const uif=(el,st)=>{let c=el,ok=0;while(c&&(c.hasAttribute('asok-if-ref')||c.hasAttribute('asok-elif-ref')||c.hasAttribute('asok-else'))){c._ai=1;let v=c.hasAttribute('asok-else')?!ok:se(c.getAttribute(c.hasAttribute('asok-if-ref')?'asok-if-ref':'asok-elif-ref'),st,c);if(v&&!ok){if(!c._n){const n=c.content.cloneNode(true);c._n=n.firstElementChild;c.parentNode.insertBefore(n,c.nextSibling);w.set(c._n,w.get(el)||{state:st,refs:{}});init(c._n)}ok=1}else if(c._n){c._n.remove();c._n=null}c=c.nextElementSibling}};"
                "const ufo=(el,st)=>{el._ai=1;const ref=el.getAttribute('asok-for-ref'),vname=el.getAttribute('asok-for-var'),items=se(ref,st,el)||[];const s=JSON.stringify(items);if(el._ls===s)return;el._ls=s;let vn=vname,id='index';if(vn.startsWith('(')&&vn.endsWith(')')){const p=vn.slice(1,-1).split(',').map(s=>s.trim());vn=p[0];if(p.length>1)id=p[1]}if(!el._m){el._m=document.createComment('for');el.parentNode.insertBefore(el._m,el.nextSibling)}(el._ns||[]).forEach(n=>n.remove());el._ns=[];items.forEach((it,i)=>{const n=el.content.cloneNode(true),child=n.firstElementChild,sub=rpx({[vn]:it,[id]:i},()=>us(fss(el)),st);w.set(child,{state:sub,refs:{},cleanup:[]});el.parentNode.insertBefore(n,el._m);el._ns.push(child);init(child)})};"
                "const us=(sc,rt=1)=>{const c=w.get(sc);if(!c)return;cs=sc;ub(sc,c.state);sc.querySelectorAll('*').forEach(el=>{if(el._uv)el._uv();if(el.tagName==='TEMPLATE'){const src=fss(el),s=src?w.get(src).state:c.state;if(el.hasAttribute('asok-if-ref'))uif(el,s);if(el.hasAttribute('asok-for-ref'))ufo(el,s);return}let p=el.parentElement;while(p&&p!==sc){if(p&&p.hasAttribute('asok-state-ref'))return;p=p.parentElement}const src=fss(el);if(src)ub(el,w.get(src).state)});cs=null;if(rt&&(c._ts||[]).forEach(t=>us(t,0)))return};"
                "const rpx=(obj,cb,st)=>{if(!obj||typeof obj!=='object'||obj._isProxy)return obj;return new Proxy(obj,{get(t,p){if(p==='_isProxy')return true;const v=(p in t)?t[p]:(st?st[p]:undefined);if(typeof v==='function'){if(['push','pop','splice','shift','unshift','reverse','sort'].includes(p))return(...args)=>{const r=v.apply(t,args);cb();return r};return v.bind(t)}return rpx(v,cb,st)},has(t,p){return p in t||(st&&p in st)},set(t,p,v){if(p in t){if(t[p]===v)return true;t[p]=v;cb();return true}if(st&&p in st){st[p]=v;return true}t[p]=v;cb();return true}})};"
                "const is=(el)=>{if(el._ai)return;const a=el.getAttribute('asok-state-ref');try{const s=rpx(se(a,{},el)||{},()=>us(el));w.set(el,{state:s,cleanup:[],refs:{},_ts:[]});el._ai=1;if(el.hasAttribute('asok-init-ref'))es(el.getAttribute('asok-init-ref'),s,null,el);us(el)}catch(e){}};"
                "const im=(el)=>{if(el._ami)return;const m=el.getAttribute('asok-model'),sc=fss(el);if(!m||!sc)return;const s=w.get(sc).state;el._ami=1;"
                "const getP=(o,p)=>p.split('.').reduce((a,k)=>a&&a[k],o);const setP=(o,p,v)=>{const k=p.split('.'),l=k.pop(),t=k.reduce((a,x)=>a[x]=a[x]||{},o);t[l]=v};"
                "el._uv=()=>{const v=getP(s,m),dv=(v!==undefined&&v!==null)?v:'';if(el.value!==String(dv)&&document.activeElement!==el){if(el.type==='checkbox')el.checked=!!dv;else if(el.type==='radio')el.checked=el.value===dv;else el.value=dv}};"
                "el._uv();const h=()=>{if(el.type==='checkbox')setP(s,m,el.checked);else if(el.type==='radio'){if(el.checked)setP(s,m,el.value)}else setP(s,m,el.value)};"
                "el.addEventListener('input',h);el.addEventListener('change',h);w.get(sc).cleanup.push(()=>{el.removeEventListener('input',h);el.removeEventListener('change',h)})};"
                "const ie=(el)=>{if(el._aei)return;const sc=fss(el);if(!sc)return;const s=w.get(sc).state;el._aei=1;Array.from(el.attributes).forEach(a=>{if(!a.name.startsWith('asok-on-ref:'))return;"
                "const en=a.name.substring(12),ref=a.value,[ev,...mods]=en.split('.'),h=(e)=>{if(mods.includes('prevent'))e.preventDefault();if(mods.includes('stop'))e.stopPropagation();"
                "if(mods.some(m=>['enter','escape','space','tab'].includes(m))&&!mods.some(m=>e.key.toLowerCase()===m))return;es(ref,s,e,el)};"
                "if(mods.includes('outside')){const oh=(e)=>{if(el.offsetWidth>0&&!el.contains(e.target)&&(!el._st||Date.now()-el._st>50))h(e)};document.addEventListener('click',oh);w.get(sc).cleanup.push(()=>document.removeEventListener('click',oh))}else{"
                "const deb=mods.find(m=>m.startsWith('debounce')),ms=deb?parseInt(deb.split('-')[1])||300:0;if(ms){let t;const dh=(e)=>{clearTimeout(t);t=setTimeout(()=>h(e),ms)};el.addEventListener(ev,dh);w.get(sc).cleanup.push(()=>el.removeEventListener(ev,dh))}"
                "else{el.addEventListener(ev,h);w.get(sc).cleanup.push(()=>el.removeEventListener(ev,h))}}});};"
                "const ifetch=(el)=>{if(el._afe)return;const url=el.getAttribute('asok-fetch'),as=el.getAttribute('asok-fetch-as')||'data',on=el.getAttribute('asok-fetch-on')||'load',sc=fss(el);if(!url||!sc)return;const s=w.get(sc).state;el._afe=1;"
                "const doFetch=async()=>{try{s.loading=true;s.error=null;const r=await fetch(url);if(!r.ok)throw new Error(r.statusText);const d=await r.json();s[as]=d;s.loading=false}catch(e){s.error=e.message;s.loading=false}};"
                "if(on==='load'){doFetch()}else{const h=()=>doFetch();el.addEventListener(on,h);w.get(sc).cleanup.push(()=>el.removeEventListener(on,h))}};"
                "const ifa=(el)=>{if(el._afea)return;const ref=el.getAttribute('asok-fetch-async-ref'),on=el.getAttribute('asok-fetch-on')||'click',sc=fss(el);if(!ref||!sc)return;const s=w.get(sc).state;el._afea=1;"
                "const doAsync=async()=>{try{s.loading=true;s.error=null;await es(ref,s,null,el);s.loading=false}catch(e){s.error=e.message;s.loading=false}};"
                "const h=()=>doAsync();el.addEventListener(on,h);w.get(sc).cleanup.push(()=>el.removeEventListener(on,h))};"
                "const cleanupOld=(r)=>{if(!r)return;const els=[r,...r.querySelectorAll('*')];els.forEach(el=>{const ctx=w.get(el);if(ctx&&ctx.cleanup){ctx.cleanup.forEach(fn=>{try{fn()}catch(e){}});ctx.cleanup=[]}})};"
                "const resetFlags=(r)=>{if(!r)return;const els=[r,...r.querySelectorAll('*')];els.forEach(el=>{delete el._ai;delete el._ami;delete el._aei;delete el._ari;delete el._ati;delete el._afe;delete el._afea;delete el._uv;delete el._ac})};"
                "const forceInit=(r)=>{if(!r)return;resetFlags(r);init(r)};"
                "const init=(r=document)=>{const els=r===document?document.querySelectorAll('*'):[r,...r.querySelectorAll('*')];els.forEach(el=>{if(el.hasAttribute('asok-state-ref'))is(el);if(el.hasAttribute('asok-ref')&&!el._ari){const sc=fss(el);if(sc){w.get(sc).refs[el.getAttribute('asok-ref')]=el;el._ari=1}}"
                "if(el.hasAttribute('asok-teleport')&&!el._ati){const t=el.getAttribute('asok-teleport'),tg=document.querySelector(t),sc=fss(el);if(tg&&sc){const ctx=w.get(sc),n=el.content.cloneNode(true),child=n.firstElementChild;w.set(child,{state:ctx.state,refs:ctx.refs,cleanup:[],_ts:[]});ctx._ts.push(child);tg.appendChild(n);init(child);el._ati=1;el.style.display='none'}} if(el.tagName==='TEMPLATE' && !el._ai){const sc=fss(el);if(sc){const s=w.get(sc).state;if(el.hasAttribute('asok-if-ref'))uif(el,s);if(el.hasAttribute('asok-for-ref'))ufo(el,s)}}});els.forEach(el=>{const sc=fss(el);if(sc)ub(el,w.get(sc).state);if(el.hasAttribute('asok-model'))im(el);if(el.hasAttribute('asok-fetch'))ifetch(el);if(el.hasAttribute('asok-fetch-async-ref'))ifa(el);if(Array.from(el.attributes).some(a=>a.name.startsWith('asok-on-ref:')))ie(el)});if(r===document)document.querySelectorAll('[asok-cloak]').forEach(e=>e.removeAttribute('asok-cloak'))};"
                "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>init());else init();"
                "if(window.Asok){const oi=window.Asok.init;window.Asok.init=(el)=>{if(oi)oi(el);init(el);};}"
                "document.addEventListener('asok:success',e=>{if(e.detail&&e.detail.target)init(e.detail.target);});window.Asok=window.Asok||{};window.AsokDirectives={init,forceInit,cleanupOld,resetFlags,version:'1.0.0',w};window.Asok.store=st; \n"
                "})(); \n"
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
                '.catch(function(){m=""})},1000)})();\n'
                "</script>"
            )

        # 6.5 CSP Error Warning (when directives detected but unsafe-eval not enabled)
        if getattr(request, "_asok_csp_error", False) and not getattr(
            request, "_asok_csp_error_done", False
        ):
            request._asok_csp_error_done = True
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">'
                "console.error("
                '"ASOK ERROR: Reactive directives detected but CSP unsafe-eval is disabled!\\n"+'
                '"Directives (asok-state, asok-text, asok-on:*) will NOT work.\\n\\n"+'
                '"Fix: Add CSP_UNSAFE_EVAL=true to your .env file, then restart."'
                ");\n"
                "</script>"
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

                content = re.sub(
                    r"(</body>)", inject_scripts, content, flags=re.I, count=1
                )

            # 2. For fragments, blocks, or final chunks
            elif not stream or is_block:
                # Check for clear closing tags first
                is_end = (
                    "</html>" in content.lower() or "</template>" in content.lower()
                )
                if is_end:
                    request._asok_scripts_done = True
                    request._asok_pending_scripts = ""  # Clear
                    content = content + "\n" + scripts
                else:
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
                        content = scripts + content
                    else:
                        # We seem to be between tags. Safe to append.
                        content = content + "\n" + scripts

        # Developer Toolbar (Optional)
        def is_true(val):
            if isinstance(val, str):
                return val.lower() in ("true", "yes", "1", "on")
            return bool(val)

        show_toolbar = is_true(self.config.get("TOOLBAR"))
        if "TOOLBAR" not in self.config:
            show_toolbar = is_true(self.config.get("DEBUG"))

        if show_toolbar and not is_block:
            if "</html>" in content.lower() or "</body>" in content.lower():
                try:
                    from .toolbar import DeveloperToolbar

                    toolbar = DeveloperToolbar(request, self)
                    content = toolbar.inject(content)
                except ImportError:
                    pass

        return content

    # ── Security headers ──────────────────────────────────────

    _DEFAULT_SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        # SECURITY: Restrict access to sensitive browser features
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
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

    def _security_headers(
        self, request: Optional[Any] = None, nonce: Optional[str] = None
    ) -> list[tuple[str, str]]:
        """Generate common security headers (HSTS, CSP, etc.)."""
        sec = self.config.get("SECURITY_HEADERS", True)
        if sec is False:
            return []
        base = dict(self._DEFAULT_SECURITY_HEADERS)

        # SECURITY: Only send HSTS over HTTPS (browsers ignore it over HTTP anyway)
        if request and request.scheme != "https":
            base.pop("Strict-Transport-Security", None)

        # Build CSP with configurable directives
        ws_port = self.config.get("WS_PORT", 8001)

        # Check if reactive features are used in this response to enable unsafe-eval only when needed
        # In DEBUG mode, always enable unsafe-eval for easier development with directives
        needs_eval = self.config.get("CSP_UNSAFE_EVAL", False)

        # SECURITY: Log when unsafe-eval is enabled for audit trail
        # Only log if it's a dynamic activation (not explicitly set in config)
        if (
            needs_eval
            and not self.config.get("DEBUG")
            and self.config.get("CSP_UNSAFE_EVAL") is not True
        ):
            logger.info("CSP 'unsafe-eval' dynamically enabled for reactive features")

        # Default CSP directives
        csp_directives = {
            "default-src": ["'self'"],
            "img-src": [
                "'self'",
                "data:",
                "blob:",
            ],  # Allow data and blob URIs for image previews
            "style-src": ["'self'", "'unsafe-inline'"],
            "connect-src": ["'self'"],
            "object-src": ["'none'"],
            "base-uri": ["'self'"],
            "form-action": ["'self'"],
            "frame-ancestors": ["'none'"],
        }

        # Add host-specific connect-src if possible
        if request and hasattr(request, "host"):
            host = request.host.split(":")[0]
            csp_directives["connect-src"].extend(
                [
                    f"ws://{host}:{ws_port}",
                    f"wss://{host}",
                    f"ws://{request.host}",
                    f"wss://{request.host}",
                ]
            )
        else:
            csp_directives["connect-src"].extend(
                [
                    f"ws://127.0.0.1:{ws_port}",
                    f"ws://localhost:{ws_port}",
                    f"ws://0.0.0.0:{ws_port}",
                ]
            )

        # Add script-src based on nonce and reactive needs
        script_src = ["'self'"]
        if nonce:
            # Use 'strict-dynamic' with nonce for CSP Level 3 browsers.
            # 'self' is kept as fallback for older browsers that don't support strict-dynamic.
            # Note: 'unsafe-inline' is ignored when nonce is present, so we don't include it.
            script_src.extend([f"'nonce-{nonce}'", "'strict-dynamic'"])

        if needs_eval:
            script_src.append("'unsafe-eval'")

        if nonce:
            csp_directives["script-src"] = script_src
        else:
            csp_directives["script-src"] = ["'self'"]

        # Allow users to extend or override CSP directives via config
        user_csp = self.config.get("CSP", {})
        if isinstance(user_csp, dict):
            for directive, values in user_csp.items():
                if isinstance(values, str):
                    values = [values]
                if directive in csp_directives:
                    # Extend existing directive, avoiding duplicates
                    existing = csp_directives[directive]
                    for val in values:
                        if val not in existing:
                            existing.append(val)
                else:
                    # Add new directive
                    csp_directives[directive] = (
                        values if isinstance(values, list) else [values]
                    )

        # Build CSP string from directives
        csp_parts = []
        for directive, values in csp_directives.items():
            csp_parts.append(f"{directive} {' '.join(values)}")

        # SECURITY: Add report-uri for CSP violation monitoring if configured
        # This allows developers to track and respond to policy violations
        csp_report_uri = self.config.get("CSP_REPORT_URI")
        if csp_report_uri:
            csp_parts.append(f"report-uri {csp_report_uri}")

        csp = "; ".join(csp_parts) + ";"

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

        # Request Log - Filter out noise (internal, static, and browser noise)
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

        # Set request context with automatic cleanup in finally block
        token = request_var.set(request)
        try:
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
                start_response(
                    "413 Payload Too Large", [("Content-Type", "text/plain")]
                )
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
                except AbortException as abort:
                    request.status = Request._STATUS_MAP.get(
                        abort.status, f"{abort.status} Unknown"
                    )
                    content_str = self._render_error_page(
                        request, abort.status, message=abort.message
                    )
                except Exception as e:
                    from .exceptions import SecurityError

                    # Handle SecurityError (CSRF, etc.) with custom error page
                    if isinstance(e, SecurityError):
                        request.status = "403 Forbidden"
                        content_str = self._render_error_page(
                            request, 403, message=str(e)
                        )
                    else:
                        import traceback
                        import uuid

                        # SECURITY: Generate unique error ID
                        error_id = str(uuid.uuid4())[:8]

                        logger.error(
                            "[ERROR-ID:%s] Admin Dispatch Exception: %s\n%s",
                            error_id,
                            str(e),
                            traceback.format_exc(),
                        )
                        if self.config.get("DEBUG"):
                            start_response(
                                "500 Internal Server Error",
                                [("Content-Type", "text/plain")],
                            )
                            return [
                                f"[ERROR-ID:{error_id}]\n\nADMIN ERROR: {str(e)}\n\n{traceback.format_exc()}".encode()
                            ]
                        request.status = "500 Internal Server Error"
                        # SECURITY: Generic message in production with tracking ID
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
            # logger.debug("  Resolved: %s (Params: %s)", page_file, route_params)

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

            # Execution
            content_str = ""
            try:
                module = None
                if page_file.endswith(".py") or page_file.endswith(".pyc"):
                    module = self._load_module(page_file)

                tpl_root = self._tpl_root

                def core_layer(req):
                    # CSRF (moved inside core_layer to allow middleware bypass)
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
                            # For API requests, return JSON error instead of HTML page
                            if req.path.startswith("/api"):
                                return req.api_error(
                                    "CSRF validation failed.", status=403
                                )

                            from .exceptions import SecurityError

                            raise SecurityError("CSRF validation failed.")

                        # Security: Rotate CSRF token for the NEXT request
                        req.csrf_token_value = secrets.token_hex(32)

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
                            action_name = (
                                req.form.get("_action")
                                or req.args.get("_action")
                                or req.args.get("action")
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

                            # Security: Automatic block validation
                            # If a block is requested (X-Block), validate its name strictly BEFORE execution
                            # to prevent data processing (side effects) on invalid targets.
                            block_header = req.environ.get("HTTP_X_BLOCK")
                            if block_header:
                                names = [b.strip() for b in block_header.split(",")]
                                for bname in names:
                                    if not bname:
                                        continue
                                    if bname.startswith("#") or not (
                                        bname.replace("_", "")
                                        .replace("-", "")
                                        .isalnum()
                                    ):
                                        msg = f"Invalid block name format: '{bname}'. Only alphanumeric characters, underscores and dashes are allowed (no # prefix)."
                                        logger.warning(
                                            f"CONSISTENCY ERROR: {msg} (from {req.ip})"
                                        )
                                        req.abort(400, msg)

                            if action_name:
                                action_func = getattr(
                                    module, f"action_{action_name}", None
                                )
                                if callable(action_func):
                                    # Security: Mandatory CSRF verification for all actions.
                                    # Actions are intended for UI-driven state changes.
                                    # For public APIs/Webhooks, use a standard post() method instead.
                                    req.verify_csrf()
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
                with request_context(request):
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
            except RedirectException as redir:
                # Save SQL stats for the toolbar to see across redirect
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
                        # Force session save NOW — _cookie_headers may not have
                        # been called yet and we must persist before the redirect.
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
                start_response(
                    status_map.get(redir.status, f"{redir.status} Found"), headers
                )
                return [b""]
            except AbortException as abort:
                request.status = Request._STATUS_MAP.get(
                    abort.status, f"{abort.status} Unknown"
                )
                content_str = self._render_error_page(
                    request, abort.status, message=abort.message
                )
                pass
            except Exception as e:
                from .exceptions import (
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
                    raise e
            except Exception as e:
                import traceback
                import uuid

                # SECURITY: Generate unique error ID for tracking
                error_id = str(uuid.uuid4())[:8]

                # Log full details internally with error ID
                logger.error(
                    "[ERROR-ID:%s] Unhandled Exception: %s\n%s",
                    error_id,
                    str(e),
                    traceback.format_exc(),
                )

                if self.config.get("DEBUG"):
                    # Debug: Show full error with ID
                    start_response(
                        "500 Internal Server Error", [("Content-Type", "text/plain")]
                    )
                    return [
                        f"[ERROR-ID:{error_id}]\n\nERROR: {str(e)}\n\n{traceback.format_exc()}".encode()
                    ]

                # SECURITY: Production - generic message with tracking ID
                body = self._render_error_page(
                    request,
                    500,
                    message=f"An unexpected error occurred. Error ID: {error_id}. Please contact support with this ID if the problem persists.",
                )
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
                headers += self._security_headers(
                    request=request, nonce=getattr(request, "nonce", None)
                )

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
            headers += self._security_headers(
                request=request, nonce=getattr(request, "nonce", None)
            )
            headers += environ.get("asok.extra_headers", [])
            headers += request.response_headers

            # Always expose the (potentially new) CSRF token for the JS engine
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

                # Send debug stats via headers for the toolbar to pick up (AJAX)
                show_toolbar = self.config.get("DEBUG") or self.config.get("TOOLBAR")
                if show_toolbar and hasattr(request, "_asok_sql_log"):
                    sql_log = request._asok_sql_log
                    headers.append(("X-Asok-SQL-Count", str(len(sql_log))))
                    try:
                        headers.append(("X-Asok-SQL-Log", json.dumps(sql_log)))
                    except Exception:
                        pass
                    # Also persist in session for the next full page load
                    # (covers non-AJAX form submissions returning a block)
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

                # Ensure JS can read these headers
                exposed = [
                    h[1] for h in headers if h[0] == "Access-Control-Expose-Headers"
                ]
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
        finally:
            request_var.reset(token)
