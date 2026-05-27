from __future__ import annotations

import importlib.util
import json
import logging
import os
import secrets
import sys
from typing import Any, Callable, Optional

from ..middleware import rate_limit_middleware
from ..orm import Model
from ..session import SessionStore
from .asgi import ASGIMixin
from .assets import AssetMixin
from .errors import ErrorRendererMixin
from .lifecycle import LifecycleMixin
from .loaders import LoaderMixin
from .routing import RoutingMixin
from .security import SecurityMixin
from .static import StaticMixin
from .wsgi import WSGIMixin

logger = logging.getLogger("asok.core")


class Asok(
    RoutingMixin,
    SecurityMixin,
    AssetMixin,
    LifecycleMixin,
    LoaderMixin,
    StaticMixin,
    ErrorRendererMixin,
    WSGIMixin,
    ASGIMixin,
):
    """The central application class for the Asok framework.

    Manages configuration, routing, middleware, and request lifecycle.
    Acts as the main entry point for your web application.
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
        # Static cache with LRU eviction: path -> (content, mimetype, etag, last_access_time)
        self._static_cache: dict[str, tuple[bytes, str, str, float]] = {}
        self._static_cache_size: int = 0
        self._static_cache_max: int = 50 * 1024 * 1024  # 50 MB max
        self._template_cache: dict[str, str] = {}
        self._middleware_chain: Optional[Callable] = None

        # Initial logger (console default)
        from ..logger import get_logger

        self.logger = get_logger("asok", config=self.config)

        self.setup()

    def setup(self) -> None:
        """Configure the application environment, load models, and prepare internal states."""

        src_path = os.path.join(self.root_dir, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

        # 2. Re-configure Logger after .env loading
        from ..logger import get_logger

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
                # SECURITY: Generate cryptographically secure random key for each run
                # This is session-only and will change on restart (intentional for dev safety)
                sec_key = secrets.token_hex(32)
                logger.warning(
                    "Running with auto-generated SECRET_KEY (DEBUG mode). "
                    "Set SECRET_KEY in your .env before deploying to production. "
                    "SECURITY WARNING: The auto-generated key changes on restart and "
                    "will invalidate all sessions. Never use DEBUG mode in production."
                )
            else:
                raise RuntimeError(
                    "SECRET_KEY environment variable is required in production. "
                    "Set it in your .env file or environment: SECRET_KEY=your-secret-key"
                )

        if not self.config.get("DEBUG") and (not sec_key or len(sec_key) < 32):
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY must be at least 32 characters long in production. "
                "Current key is too weak. Please generate a strong key using 'secrets.token_hex(32)'."
            )

        self.config["SECRET_KEY"] = sec_key
        os.environ["SECRET_KEY"] = sec_key
        self.config.setdefault("WS_PORT", 8001)
        self.config.setdefault("CSP_UNSAFE_EVAL", False)

        # 4. Global Config Overrides from Environment
        for key in list(self.config.keys()):
            env_val = os.environ.get(f"ASOK_{key}", os.environ.get(key))
            if env_val is not None:
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
        # SECURITY: Add rate limiting middleware first (runs before user middlewares)
        self.middleware_handlers.insert(0, rate_limit_middleware)

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

        # Load Components
        comp_dir = os.path.join(self.root_dir, self.dirs["COMPONENTS"])
        if os.path.exists(comp_dir):
            import sys as _sys

            for filename in sorted(os.listdir(comp_dir)):
                if (
                    filename.endswith(".py") or filename.endswith(".pyc")
                ) and not filename.startswith("__"):
                    filepath = os.path.join(comp_dir, filename)
                    ext_len = 4 if filename.endswith(".pyc") else 3
                    mod_name = f"comp_{filename[:-ext_len]}"
                    spec = importlib.util.spec_from_file_location(mod_name, filepath)
                    mod = importlib.util.module_from_spec(spec)
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

        self._static_dirs = frozenset(["images", "css", "js", "uploads"])
        self._partials_path = os.path.join(self.root_dir, self.dirs["PARTIALS"])
        self._tpl_root = os.path.abspath(os.path.join(self.root_dir, "src/partials"))

        # Scan all templates in src/ for directives to enable the JS engine globally
        self.directives_enabled = False
        src_dir = os.path.join(self.root_dir, "src")
        if os.path.isdir(src_dir):
            directive_markers = [
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
            try:
                for root_path, _, files in os.walk(src_dir):
                    for file in files:
                        if file.endswith((".html", ".asok")):
                            filepath = os.path.join(root_path, file)
                            try:
                                with open(
                                    filepath, "r", encoding="utf-8", errors="ignore"
                                ) as f:
                                    content = f.read()
                                if any(
                                    marker in content for marker in directive_markers
                                ):
                                    self.directives_enabled = True
                                    break
                            except Exception:
                                pass
                    if self.directives_enabled:
                        break
            except Exception:
                pass

        self._static_hashes = {}

        # SECURITY: Validate SESSION_PATH to prevent path traversal
        session_path = self.config["SESSION_PATH"]
        if ".." in session_path or session_path.startswith("/"):
            raise ValueError(
                f"SESSION_PATH '{session_path}' contains path traversal sequences. "
                "Use a relative path without '..' (e.g., '.asok/sessions')"
            )

        self._session_store = SessionStore(
            backend=self.config["SESSION_BACKEND"],
            path=os.path.join(self.root_dir, session_path),
            ttl=self.config["SESSION_TTL"],
        )
        if self.config["SESSION_BACKEND"] != "redis":
            self._session_store.start_cleanup_timer(interval=3600)

        # Sync default_cache backend with environment settings loaded in setup
        from ..cache import default_cache

        env_backend = os.environ.get("ASOK_CACHE_BACKEND", "memory").lower()
        if env_backend != default_cache.backend:
            default_cache.backend = env_backend
            if env_backend == "file":
                os.makedirs(default_cache._path, exist_ok=True)
            elif env_backend == "redis":
                default_cache._init_redis()

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

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Main entry point supporting both WSGI and ASGI servers."""
        if len(args) == 2:
            return self._wsgi_call(*args, **kwargs)
        elif len(args) == 3:
            return self._asgi_call(*args, **kwargs)
        else:
            raise TypeError(
                "Invalid call signature. Expected WSGI (2 args) or ASGI (3 args)."
            )
