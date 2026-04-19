from __future__ import annotations

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

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve an item from the cache. Returns the default if not found or expired."""
        if self.backend == "file":
            return self._file_get(key, default)

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

        with self._lock:
            self._store[key] = {"value": value, "expires": expires}

    def forget(self, key: str) -> None:
        """Remove a specific key from the cache."""
        if self.backend == "file":
            return self._file_forget(key)

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

        with self._lock:
            self._store.clear()

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
