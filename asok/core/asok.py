"""
Core application class for the Asok framework.

Integrates routing, templates, ORM, WebSockets, static site generation,
and WSGI/ASGI application endpoints.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import os
import secrets
import sys
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from .extension import AsokExtension

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
from .ssg_isr import SSGISRMixin
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
    SSGISRMixin,
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

        self.version = getattr(asok, "__version__", "0.5.3")
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
            "GRAPHQL_DISABLE_INTROSPECTION": None,
            "STRICT_STATIC_TEMPLATES": False,
        }

        # Lifecycle hooks
        self._on_startup: list[Callable] = []
        self._on_shutdown: list[Callable] = []
        self._tasks: list[Any] = []
        self._executor: Optional[Any] = None

        # Caches (populated in production, bypassed in DEBUG)
        self._route_cache: dict[str, tuple[str, dict[str, str]]] = {}
        self._module_cache: dict[str, Any] = {}
        # Static cache with O(1) LRU eviction via OrderedDict: path -> (content, mimetype, etag)
        self._static_cache: OrderedDict[str, tuple[bytes, str, str]] = OrderedDict()
        self._static_cache_size: int = 0
        self._static_cache_max: int = 50 * 1024 * 1024  # 50 MB max
        self._template_cache: dict[str, str] = {}
        # Pre-computed middleware metadata (populated in _load_middlewares)
        self._has_async_middleware: bool = False
        self._middleware_handlers_reversed: list[Callable] = []

        # Initial logger (console default)
        from ..logger import get_logger

        self.logger = get_logger("asok", config=self.config)

        self.setup()

    _DIRECTIVE_MARKERS = (
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
    )

    def setup(self) -> None:
        """Configure the application environment, load models, and prepare internal states."""
        self._setup_python_path()
        self._setup_logger()
        self._ensure_package_dirs(
            self.dirs["MODELS"],
            self.dirs["MIDDLEWARES"],
            "src/routes",
        )
        self._load_env_file()
        self._setup_debug_flag()
        self._setup_docs_flag()
        self._setup_toolbar_flag()
        self._setup_secret_key()
        self._apply_env_overrides()
        self._load_middlewares()
        self._load_models()
        self._load_components()
        self._load_locales()
        self._init_paths_and_extensions()
        self._detect_directives()
        self._setup_session_store()
        self._sync_cache_backend()

    def _setup_python_path(self) -> None:
        src_path = os.path.join(self.root_dir, "src")
        if src_path not in sys.path:
            sys.path.insert(0, src_path)

    def _setup_logger(self) -> None:
        # Re-configure Logger after .env loading
        from ..logger import get_logger

        self.logger = get_logger("asok", config=self.config)

    def _load_env_file(self) -> None:
        # Load .env if exists to populate os.environ early
        env_path = os.path.join(self.root_dir, ".env")
        if not os.path.exists(env_path):
            return
        with open(env_path) as f:
            for raw in f:
                self._apply_env_line(raw.strip())

    @staticmethod
    def _apply_env_line(line: str) -> None:
        if not line or line.startswith("#") or "=" not in line:
            return
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()

    @staticmethod
    def _bool_env(value: str) -> Optional[bool]:
        v = value.lower()
        if v == "true":
            return True
        if v == "false":
            return False
        return None

    def _setup_debug_flag(self) -> None:
        flag = self._bool_env(os.environ.get("DEBUG", ""))
        if flag is not None:
            self.config["DEBUG"] = flag

    def _setup_docs_flag(self) -> None:
        flag = self._bool_env(os.environ.get("ASOK_DOCS", os.environ.get("DOCS", "")))
        if flag is None:
            self.config["DOCS"] = self.config.get("DEBUG", True)
        else:
            self.config["DOCS"] = flag

    def _setup_toolbar_flag(self) -> None:
        flag = self._bool_env(
            os.environ.get("ASOK_TOOLBAR", os.environ.get("TOOLBAR", ""))
        )
        if flag is not None:
            self.config["TOOLBAR"] = flag

    def _setup_secret_key(self) -> None:
        sec_key = os.getenv("SECRET_KEY") or self._generate_secret_key_for_debug()
        self._validate_secret_key(sec_key)
        self.config["SECRET_KEY"] = sec_key
        os.environ["SECRET_KEY"] = sec_key
        self.config.setdefault("WS_PORT", 8001)
        self.config.setdefault("CSP_UNSAFE_EVAL", False)

    def _generate_secret_key_for_debug(self) -> str:
        if not self.config.get("DEBUG"):
            raise RuntimeError(
                "SECRET_KEY environment variable is required in production. "
                "Set it in your .env file or environment: SECRET_KEY=your-secret-key"
            )
        # SECURITY: random per-run key; rotates on restart (intentional for dev).
        sec_key = secrets.token_hex(32)
        logger.warning(
            "Running with auto-generated SECRET_KEY (DEBUG mode). "
            "Set SECRET_KEY in your .env before deploying to production. "
            "SECURITY WARNING: The auto-generated key changes on restart and "
            "will invalidate all sessions. Never use DEBUG mode in production."
        )
        return sec_key

    def _validate_secret_key(self, sec_key: str) -> None:
        if self.config.get("DEBUG"):
            return
        if not sec_key or len(sec_key) < 32:
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY must be at least 32 characters long in production. "
                "Current key is too weak. Please generate a strong key using 'secrets.token_hex(32)'."
            )
        if sec_key == "change-me-to-a-very-secure-production-secret-key-32-chars":
            raise ValueError(
                "SECURITY ERROR: SECRET_KEY is set to the default boilerplate placeholder key in production. "
                "You must change SECRET_KEY to a secure randomly generated value (e.g. using 'secrets.token_hex(32)') before deploying."
            )

    def _apply_env_overrides(self) -> None:
        for key in list(self.config.keys()):
            env_val = os.environ.get(f"ASOK_{key}", os.environ.get(key))
            if env_val is not None:
                self._apply_env_override(key, env_val)

    def _apply_env_override(self, key: str, env_val: str) -> None:
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

    def _load_middlewares(self) -> None:
        # SECURITY: rate limiting must run before user middlewares.
        self.middleware_handlers.insert(0, rate_limit_middleware)
        mw_dir = os.path.join(self.root_dir, self.dirs["MIDDLEWARES"])
        for filepath, filename in self._iter_py_modules(mw_dir):
            mod = self._load_module_from_path(f"mw_{filename}", filepath)
            if hasattr(mod, "handle"):
                self.middleware_handlers.append(mod.handle)
        # Pre-compute once; avoids per-request inspect.iscoroutinefunction() scans.
        self._has_async_middleware = any(
            inspect.iscoroutinefunction(mw) for mw in self.middleware_handlers
        )
        self._middleware_handlers_reversed = list(reversed(self.middleware_handlers))

    def _load_models(self) -> None:
        model_dir = os.path.join(self.root_dir, self.dirs["MODELS"])
        for filepath, filename in self._iter_py_modules(model_dir):
            mod = self._load_module_from_path(f"model_{filename}", filepath)
            self._collect_models_from_module(mod)

    def _collect_models_from_module(self, mod: Any) -> None:
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, Model) and attr is not Model:
                self.models.append(attr)

    def _load_components(self) -> None:
        comp_dir = os.path.join(self.root_dir, self.dirs["COMPONENTS"])
        for filepath, filename in self._iter_py_modules(comp_dir):
            ext_len = 4 if filename.endswith(".pyc") else 3
            mod_name = f"comp_{filename[:-ext_len]}"
            mod = self._load_module_from_path(mod_name, filepath, register=True)
            del mod  # registered via sys.modules

    @staticmethod
    def _iter_py_modules(directory: str):
        if not os.path.exists(directory):
            return
        for filename in sorted(os.listdir(directory)):
            if Asok._is_loadable_py_file(filename):
                yield os.path.join(directory, filename), filename

    @staticmethod
    def _is_loadable_py_file(filename: str) -> bool:
        if filename.startswith("__"):
            return False
        return filename.endswith(".py") or filename.endswith(".pyc")

    @staticmethod
    def _load_module_from_path(
        mod_name: str, filepath: str, register: bool = False
    ) -> Any:
        spec = importlib.util.spec_from_file_location(mod_name, filepath)
        mod = importlib.util.module_from_spec(spec)
        if register:
            sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    def _load_locales(self) -> None:
        locale_dir = os.path.join(self.root_dir, self.dirs["LOCALES"])
        if not os.path.exists(locale_dir):
            return
        for filename in os.listdir(locale_dir):
            if not filename.endswith(".json"):
                continue
            lang = filename[:-5]
            with open(os.path.join(locale_dir, filename), "r", encoding="utf-8") as f:
                self.locales[lang] = json.load(f)

    def _ensure_extension_containers(self) -> None:
        defaults: list[tuple[str, Any]] = [
            ("extensions", dict),
            ("_extension_pages_paths", list),
            ("_extension_template_paths", list),
            ("_extension_static_paths", list),
            ("_static_hashes", dict),
        ]
        for attr, factory in defaults:
            if not hasattr(self, attr):
                setattr(self, attr, factory())

    def _init_paths_and_extensions(self) -> None:
        self._static_dirs = frozenset(["images", "css", "js", "uploads"])
        self._partials_path = os.path.join(self.root_dir, self.dirs["PARTIALS"])
        self._tpl_root = os.path.abspath(os.path.join(self.root_dir, "src/partials"))
        self._ensure_extension_containers()
        # Cached search path lists (invalidated on extension registration).
        self.__pages_search_paths_cache: Optional[list[str]] = None
        self.__template_search_paths_cache: Optional[list[str]] = None
        self.__static_search_paths_cache: Optional[list[str]] = None

    def _detect_directives(self) -> None:
        self.directives_enabled = False
        src_dir = os.path.join(self.root_dir, "src")
        if not os.path.isdir(src_dir):
            return
        try:
            self._scan_directives(src_dir)
        except Exception:
            pass

    def _scan_directives(self, src_dir: str) -> None:
        for root_path, _, files in os.walk(src_dir):
            for file in files:
                if not file.endswith((".html", ".asok")):
                    continue
                if self._file_has_directives(os.path.join(root_path, file)):
                    self.directives_enabled = True
                    return

    def _file_has_directives(self, filepath: str) -> bool:
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return False
        return any(marker in content for marker in self._DIRECTIVE_MARKERS)

    def _setup_session_store(self) -> None:
        # SECURITY: forbid path traversal in SESSION_PATH.
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

    def _sync_cache_backend(self) -> None:
        from ..cache import default_cache

        env_backend = os.environ.get("ASOK_CACHE_BACKEND", "memory").lower()
        if env_backend != default_cache.backend:
            default_cache.backend = env_backend
            if env_backend == "file":
                os.makedirs(default_cache._path, exist_ok=True)
            elif env_backend == "redis":
                default_cache._init_redis()

        self._check_cache_backend_security(default_cache.backend)

    def _check_cache_backend_security(self, backend: str) -> None:
        if self.config.get("DEBUG", False):
            return
        if backend == "memory":
            logger.warning(
                "SECURITY WARNING: Cache backend is set to 'memory' in production. "
                "In multi-process deployments (like Gunicorn), rate limiting and caching "
                "will be per-process instead of global. Consider configuring 'redis' or 'file' backend."
            )

    def _ensure_package_dirs(self, *dirs: str) -> None:
        """Create empty __init__.py in directories if they exist but are not Python packages."""
        for d in dirs:
            self._ensure_package_dir(d)

    def _ensure_package_dir(self, d: str) -> None:
        path = os.path.join(self.root_dir, d)
        if not os.path.isdir(path):
            return
        init_file = os.path.join(path, "__init__.py")
        if os.path.exists(init_file) or os.path.exists(init_file + "c"):
            return
        try:
            with open(init_file, "w"):
                pass
        except Exception as e:
            logger.warning(f"Could not create __init__.py in {d}: {e}")

    @property
    def _pages_search_paths(self) -> list[str]:
        if self.__pages_search_paths_cache is None:
            paths = [os.path.join(self.root_dir, self.dirs.get("PAGES", "src/pages"))]
            paths.extend(self._extension_pages_paths)
            self.__pages_search_paths_cache = paths
        return self.__pages_search_paths_cache

    @property
    def _template_search_paths(self) -> list[str]:
        if self.__template_search_paths_cache is None:
            paths = [
                os.path.join(self.root_dir, self.dirs.get("PAGES", "src/pages")),
                os.path.join(
                    self.root_dir, self.dirs.get("COMPONENTS", "src/components")
                ),
                os.path.join(self.root_dir, self.dirs.get("PARTIALS", "src/partials")),
            ]
            paths.extend(self._extension_template_paths)
            self.__template_search_paths_cache = paths
        return self.__template_search_paths_cache

    @property
    def _static_search_paths(self) -> list[str]:
        if self.__static_search_paths_cache is None:
            partials_path = os.path.join(
                self.root_dir, self.dirs.get("PARTIALS", "src/partials")
            )
            paths = [partials_path]
            paths.extend(self._extension_static_paths)
            self.__static_search_paths_cache = paths
        return self.__static_search_paths_cache

    @staticmethod
    def _parse_cors_origins_static(cors_origins: Any) -> list[str]:
        if isinstance(cors_origins, str):
            return [o.strip() for o in cors_origins.split(",") if o.strip()]
        return _parse_cors_iterable(cors_origins)

    def _invalidate_search_path_caches(self) -> None:
        self.__pages_search_paths_cache = None
        self.__template_search_paths_cache = None
        self.__static_search_paths_cache = None
        if hasattr(self, "_route_cache"):
            self._route_cache.clear()

    _FORBIDDEN_UNIX_ROOTS = (
        "/etc",
        "/sys",
        "/proc",
        "/dev",
        "/boot",
        "/root",
        "/var/run",
    )
    _FORBIDDEN_WIN_ROOTS = ("c:\\windows", "c:\\winnt", "\\windows\\system32")

    def register_extension(
        self, extension: type[AsokExtension] | AsokExtension
    ) -> None:
        """Register a community extension class or instance with the application."""
        from .extension import AsokExtension

        if isinstance(extension, type) and issubclass(extension, AsokExtension):
            extension = extension()

        ext_name = extension.__class__.__name__
        if ext_name not in self.extensions:
            extension.init_app(self)

        self._register_extension_path(
            extension.get_pages_path(), self._extension_pages_paths, "page"
        )
        self._register_extension_path(
            extension.get_templates_path(), self._extension_template_paths, "template"
        )
        self._register_extension_path(
            extension.get_static_path(), self._extension_static_paths, "static"
        )

    def _register_extension_path(
        self, path: Optional[str], collection: list[str], label: str
    ) -> None:
        if not path or not os.path.isdir(path):
            return
        abs_path = os.path.abspath(path)
        if not self._is_safe_extension_path(abs_path):
            raise ValueError(
                f"Extension {label} path cannot be a system directory: {abs_path}"
            )
        collection.append(abs_path)
        self._invalidate_search_path_caches()

    @classmethod
    def _is_safe_extension_path(cls, abs_p: str) -> bool:
        if abs_p in ("/", "\\") or os.path.dirname(abs_p) == abs_p:
            return False
        if abs_p.startswith(cls._FORBIDDEN_UNIX_ROOTS):
            return False
        abs_p_lower = abs_p.lower()
        return not any(abs_p_lower.startswith(w) for w in cls._FORBIDDEN_WIN_ROOTS)

    def register_extensions(
        self, extensions: list[type[AsokExtension] | AsokExtension]
    ) -> None:
        """Register a list of community extensions with the application."""
        for extension in extensions:
            self.register_extension(extension)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Main entry point supporting both WSGI and ASGI servers."""
        if len(args) == 1:
            scope = args[0]

            async def asgi_app(receive: Callable, send: Callable) -> None:
                await self._asgi_call(scope, receive, send)

            return asgi_app
        elif len(args) == 2:
            return self._wsgi_call(*args, **kwargs)
        elif len(args) == 3:
            return self._asgi_call(*args, **kwargs)
        else:
            raise TypeError(
                "Invalid call signature. Expected WSGI (2 args) or ASGI (3 args)."
            )


def _parse_cors_iterable(cors_origins: Any) -> list[str]:
    try:
        return [str(o).strip() for o in cors_origins]
    except TypeError:
        return []
