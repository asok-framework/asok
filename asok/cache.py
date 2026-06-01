from __future__ import annotations

import functools
import hashlib
import json
import os
import threading
import time
from typing import Any, Optional


class Cache:
    """Unified caching interface supporting both in-memory and file-based persistence."""

    def __init__(
        self,
        backend: str = "memory",
        path: str = ".cache",
        prefix: str = "",
        namespace: str = "",
    ):
        """Initialize the cache store.

        Args:
            backend: The storage backend to use ('memory' or 'file').
            path: The directory for file-based cache storage.
            prefix: Optional global prefix for all keys.
            namespace: Optional subgrouping for keys.
        """
        self.backend = backend
        self.prefix = prefix
        self.namespace = namespace
        self._path = path
        self._store: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

        if backend == "file":
            os.makedirs(path, exist_ok=True)
        elif backend == "redis":
            self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis
        except ImportError:
            raise ImportError(
                "The 'redis' library is required to use the Redis cache backend. "
                "Install it using 'pip install asok[redis]'."
            )
        redis_url = (
            os.environ.get("ASOK_REDIS_URL")
            or os.environ.get("REDIS_URL")
            or "redis://localhost:6379/0"
        )
        self._redis = redis.Redis.from_url(redis_url)

    def _get_redis_client(self):
        if not hasattr(self, "_redis") or self._redis is None:
            self._init_redis()
        return self._redis

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an item from the cache. Returns the default if not found or expired."""
        if self.backend == "file":
            return self._file_get(key, default)
        elif self.backend == "redis":
            return self._redis_get(key, default)

        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return default
            if entry["expires"] and time.time() > entry["expires"]:
                del self._store[key]
                return default
            return entry["value"]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store an item in the cache with an optional time-to-live in seconds."""
        expires = (time.time() + ttl) if ttl else None

        if self.backend == "file":
            return self._file_set(key, value, expires)
        elif self.backend == "redis":
            return self._redis_set(key, value, ttl)

        with self._lock:
            self._store[key] = {"value": value, "expires": expires}

    def forget(self, key: str) -> None:
        """Remove a specific key from the cache."""
        if self.backend == "file":
            return self._file_forget(key)
        elif self.backend == "redis":
            return self._redis_forget(key)

        with self._lock:
            self._store.pop(key, None)

    def remember(self, key: str, ttl: Optional[int], fn: Any) -> Any:
        """Get a cached value, or compute and store it if missing.

        Args:
            key: The cache key.
            ttl: Time-to-live in seconds (None for no expiry).
            fn: A callable that returns the value to cache.

        Returns:
            The cached or freshly computed value.
        """
        value = self.get(key, _SENTINEL)
        if value is not _SENTINEL:
            return value
        value = fn()
        self.set(key, value, ttl)
        return value

    def has(self, key: str) -> bool:
        """Check if a key exists in the cache and is not expired."""
        return self.get(key, _SENTINEL) is not _SENTINEL

    def flush(self) -> None:
        """Clear all items from the cache."""
        if self.backend == "file":
            return self._file_flush()
        elif self.backend == "redis":
            return self._redis_flush()

        with self._lock:
            self._store.clear()

    # --- Redis backend ---

    def _redis_key(self, key: str) -> str:
        return f"{self.namespace}:{self.prefix}:{key}"

    def _redis_get(self, key: str, default: Any = None) -> Any:
        client = self._get_redis_client()
        rkey = self._redis_key(key)
        try:
            val = client.get(rkey)
            if val is None:
                return default
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            return json.loads(val)
        except Exception:
            return default

    def _redis_set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        client = self._get_redis_client()
        rkey = self._redis_key(key)
        try:
            val_str = json.dumps(value)
            if ttl:
                client.setex(rkey, ttl, val_str)
            else:
                client.set(rkey, val_str)
        except Exception:
            pass

    def _redis_forget(self, key: str) -> None:
        client = self._get_redis_client()
        rkey = self._redis_key(key)
        try:
            client.delete(rkey)
        except Exception:
            pass

    def _redis_flush(self) -> None:
        client = self._get_redis_client()
        pattern = self._redis_key("*")
        try:
            keys = client.keys(pattern)
            if keys:
                client.delete(*keys)
        except Exception:
            pass

    # --- File backend ---

    def _key_path(self, key: str) -> str:
        """Securely map a cache key to a file path using hashing."""
        # Use namespace and prefix to prevent collisions
        full_key = f"{self.namespace}:{self.prefix}:{key}"
        safe_name = hashlib.sha256(full_key.encode()).hexdigest()
        return os.path.join(self._path, safe_name + ".json")

    def _file_get(self, key, default=None):
        path = self._key_path(key)
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
            if entry["expires"] and time.time() > entry["expires"]:
                os.remove(path)
                return default
            return entry["value"]
        except (json.JSONDecodeError, KeyError, OSError):
            return default

    def _file_set(self, key, value, expires):
        path = self._key_path(key)
        # SECURITY: Restrictive permissions (owner-only read/write) to prevent
        # other system users from reading cached data — same pattern as SessionStore.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"value": value, "expires": expires}, f)

    def _file_forget(self, key):
        path = self._key_path(key)
        if os.path.exists(path):
            os.remove(path)

    def _file_flush(self):
        if os.path.isdir(self._path):
            for fname in os.listdir(self._path):
                if fname.endswith(".json"):
                    os.remove(os.path.join(self._path, fname))


_SENTINEL = object()

# Global default cache instance
_backend = os.environ.get("ASOK_CACHE_BACKEND", "memory").lower()
default_cache = Cache(backend=_backend)


def cache_page(
    ttl: int = 60, key_prefix: str = "page_", cache_instance: Optional[Cache] = None
):
    """
    Decorator to cache the HTTP response of a view function.
    Only caches GET requests.

    SECURITY: Cache key length limits prevent DoS.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(request, *args, **kwargs):
            if getattr(request, "method", "GET") != "GET":
                return func(request, *args, **kwargs)

            cache = cache_instance or default_cache
            # Provide a safe fallback if request doesn't have path for some reason
            path = getattr(request, "path", "")
            qs = getattr(request, "query_string", "")

            # SECURITY: Limit path and query string length to prevent DoS (max 2000 chars each)
            if len(path) > 2000:
                path = path[:2000]
            if len(qs) > 2000:
                qs = qs[:2000]

            full_path = f"{path}?{qs}" if qs else path
            cache_key = f"{key_prefix}{full_path}"

            cached = cache.get(cache_key)
            if cached is not None:
                token = getattr(request, "csrf_token_value", None)
                if isinstance(cached, str) and token:
                    return cached.replace("__ASOK_CSRF_TOKEN_PLACEHOLDER__", token)
                return cached

            response = func(request, *args, **kwargs)

            # Do not cache explicit errors or redirects if possible.
            # In Asok, view functions often return a Response object or just a string.
            status_code = getattr(response, "status", "200")
            if str(status_code).startswith("200"):
                token = getattr(request, "csrf_token_value", None)
                if isinstance(response, str) and token:
                    cached_response = response.replace(token, "__ASOK_CSRF_TOKEN_PLACEHOLDER__")
                else:
                    cached_response = response
                cache.set(cache_key, cached_response, ttl=ttl)

            return response

        return wrapper

    return decorator
