from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from typing import Any, Optional


class Session(dict):
    """A dictionary-like object representing a user session that tracks its own modification state."""

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.modified: bool = False
        self.sid: Optional[str] = None

    def __setitem__(self, key: Any, value: Any) -> None:
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        self.modified = True
        super().__delitem__(key)

    def pop(self, key: Any, *args: Any) -> Any:
        """Remove a key and return its value. Marks the session as modified."""
        self.modified = True
        return super().pop(key, *args)

    def update(self, *args: Any, **kwargs: Any) -> None:
        """Update the session with multiple key-value pairs. Marks the session as modified."""
        self.modified = True
        super().update(*args, **kwargs)

    def clear(self) -> None:
        """Remove all items from the session. Marks the session as modified."""
        self.modified = True
        super().clear()


class SessionStore:
    """Handles session persistence using various backends (memory, file, redis)."""

    def __init__(
        self,
        backend: str = "memory",
        path: str = ".asok/sessions",
        ttl: int = 86400,
        max_sessions: int = 10000,
    ):
        """Initialize the session store.

        Args:
            backend: The storage backend to use ('memory', 'file', or 'redis').
            path: The directory for file-based sessions.
            ttl: Time-to-live for sessions in seconds (default 24 hours).
            max_sessions: Maximum number of in-memory sessions (default 10000).
        """
        self.backend = backend
        self.path = path
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._memory: dict[
            str, dict[str, Any]
        ] = {}  # sid -> {"data": dict, "ts": float}

        if backend == "file":
            os.makedirs(path, exist_ok=True)
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
        elif backend == "redis":
            try:
                import redis
            except ImportError:
                raise ImportError(
                    "The 'redis' library is required to use the Redis session backend. "
                    "Install it using 'pip install asok[redis]'."
                )
            redis_url = os.environ.get("ASOK_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://localhost:6379/0"
            self._redis = redis.Redis.from_url(redis_url)

    def load(self, sid: str) -> Optional[dict[str, Any]]:
        """Load session data for the given session ID."""
        if self.backend == "file":
            return self._load_file(sid)
        elif self.backend == "redis":
            return self._load_redis(sid)
        return self._load_memory(sid)

    def save(self, sid: str, data: dict[str, Any]) -> None:
        """Persist session data for the given session ID."""
        if self.backend == "file":
            return self._save_file(sid, data)
        elif self.backend == "redis":
            return self._save_redis(sid, data)
        return self._save_memory(sid, data)

    def delete(self, sid: str) -> None:
        """Remove a session from storage."""
        if self.backend == "file":
            return self._delete_file(sid)
        elif self.backend == "redis":
            return self._delete_redis(sid)
        return self._delete_memory(sid)

    def generate_sid(self) -> str:
        """Generate a new unique session identifier using a secure cryptographically strong RNG."""
        return secrets.token_hex(32)

    def regenerate(self, sid: str) -> str:
        """Rotate the session ID while preserving data to prevent session fixation.

        Returns the new session ID.
        """
        data = self.load(sid)
        new_sid = self.generate_sid()
        if data is not None:
            self.save(new_sid, data)
        self.delete(sid)
        return new_sid

    def cleanup(self) -> int:
        """Remove all expired sessions. Returns the number of sessions purged."""
        if self.backend == "file":
            return self._cleanup_file()
        elif self.backend == "redis":
            return 0  # Managed by Redis TTL automatically
        return self._cleanup_memory()

    def _cleanup_memory(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._memory.items() if now - v["ts"] > self.ttl]
            for k in expired:
                del self._memory[k]
        return len(expired)

    def _cleanup_file(self) -> int:
        count = 0
        if not os.path.isdir(self.path):
            return 0
        now = time.time()
        for fname in os.listdir(self.path):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(self.path, fname)
            try:
                with open(fpath, "r") as f:
                    entry = json.load(f)
                if now - entry.get("ts", 0) > self.ttl:
                    os.remove(fpath)
                    count += 1
            except (json.JSONDecodeError, OSError, KeyError):
                pass
        return count

    def start_cleanup_timer(self, interval: int = 3600) -> threading.Timer:
        """Start a recurring background cleanup of expired sessions.

        Args:
            interval: Seconds between cleanups (default: 1 hour).

        Returns:
            The background timer thread.
        """

        def _run():
            self.cleanup()
            if self._cleanup_timer_running:
                self._timer = threading.Timer(interval, _run)
                self._timer.daemon = True
                self._timer.start()

        self._cleanup_timer_running = True
        self._timer = threading.Timer(interval, _run)
        self._timer.daemon = True
        self._timer.start()
        return self._timer

    def stop_cleanup_timer(self):
        """Stop the recurring cleanup timer."""
        self._cleanup_timer_running = False
        if hasattr(self, "_timer"):
            self._timer.cancel()

    # ── Memory backend ────────────────────────────────────────

    def _load_memory(self, sid):
        with self._lock:
            entry = self._memory.get(sid)
            if entry is None:
                return None
            if time.time() - entry["ts"] > self.ttl:
                del self._memory[sid]
                return None
            return entry["data"]

    def _save_memory(self, sid, data):
        """Save session data to memory.

        SECURITY: Session data size limits prevent DoS.
        """
        # SECURITY: Limit session data size to prevent DoS (max 100KB per session)
        try:
            data_str = json.dumps(data)
            if len(data_str) > 100_000:
                import logging

                logging.getLogger("asok.session").warning(
                    "Session data too large (%d bytes), truncating", len(data_str)
                )
                # Truncate to first 1000 items if dict
                if isinstance(data, dict) and len(data) > 1000:
                    data = dict(list(data.items())[:1000])
        except (TypeError, ValueError):
            pass

        with self._lock:
            self._memory[sid] = {"data": dict(data), "ts": time.time()}
            # Evict oldest sessions if over capacity
            if len(self._memory) > self.max_sessions:
                now = time.time()
                # First purge expired
                expired = [
                    k for k, v in self._memory.items() if now - v["ts"] > self.ttl
                ]
                for k in expired:
                    del self._memory[k]
                # If still over, evict oldest
                while len(self._memory) > self.max_sessions:
                    oldest_key = min(self._memory, key=lambda k: self._memory[k]["ts"])
                    del self._memory[oldest_key]

    def _delete_memory(self, sid):
        with self._lock:
            self._memory.pop(sid, None)

    # ── File backend ──────────────────────────────────────────

    _RE_SAFE_SID = re.compile(r"^[a-f0-9]+$")

    def _session_file(self, sid):
        if not self._RE_SAFE_SID.match(sid):
            raise ValueError("Invalid session ID")
        return os.path.join(self.path, f"{sid}.json")

    def _load_file(self, sid):
        fpath = self._session_file(sid)
        if not os.path.isfile(fpath):
            return None
        try:
            with open(fpath, "r") as f:
                entry = json.load(f)
            if time.time() - entry.get("ts", 0) > self.ttl:
                os.remove(fpath)
                return None
            return entry.get("data")
        except (json.JSONDecodeError, OSError):
            return None

    def _save_file(self, sid, data):
        """Save session data to file.

        SECURITY: Session data size limits prevent DoS.
        """
        # SECURITY: Limit session data size to prevent DoS (max 100KB per session)
        try:
            data_str = json.dumps(data)
            if len(data_str) > 100_000:
                import logging

                logging.getLogger("asok.session").warning(
                    "Session data too large (%d bytes), truncating", len(data_str)
                )
                # Truncate to first 1000 items if dict
                if isinstance(data, dict) and len(data) > 1000:
                    data = dict(list(data.items())[:1000])
        except (TypeError, ValueError):
            pass

        fpath = self._session_file(sid)
        # Use os.open with restrictive mode (0600) before writing
        try:
            fd = os.open(fpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"data": dict(data), "ts": time.time()}, f)
        except OSError as e:
            # Fallback for systems that don't support os.open securely
            import logging
            logger = logging.getLogger("asok.session")
            logger.warning(f"Failed to create session file with secure permissions: {e}")

            with open(fpath, "w") as f:
                json.dump({"data": dict(data), "ts": time.time()}, f)
            try:
                os.chmod(fpath, 0o600)
            except OSError as chmod_err:
                # SECURITY: Log if chmod fails - session file may be world-readable!
                logger.error(
                    f"SECURITY WARNING: Failed to set secure permissions (0600) on session file {fpath}: {chmod_err}. "
                    "Session data may be exposed to other users on the system!"
                )

    def _delete_file(self, sid):
        fpath = self._session_file(sid)
        try:
            os.remove(fpath)
        except OSError:
            pass

    # ── Redis backend ──────────────────────────────────────────

    def _redis_key(self, sid: str) -> str:
        return f"session:{sid}"

    def _load_redis(self, sid: str) -> Optional[dict[str, Any]]:
        rkey = self._redis_key(sid)
        try:
            val = self._redis.get(rkey)
            if val is None:
                return None
            if isinstance(val, bytes):
                val = val.decode("utf-8")
            return json.loads(val)
        except Exception:
            return None

    def _save_redis(self, sid: str, data: dict[str, Any]) -> None:
        # SECURITY: Limit session data size to prevent DoS (max 100KB per session)
        try:
            data_str = json.dumps(data)
            if len(data_str) > 100_000:
                import logging

                logging.getLogger("asok.session").warning(
                    "Session data too large (%d bytes), truncating", len(data_str)
                )
                if isinstance(data, dict) and len(data) > 1000:
                    data = dict(list(data.items())[:1000])
                    data_str = json.dumps(data)
        except (TypeError, ValueError):
            return

        rkey = self._redis_key(sid)
        try:
            self._redis.setex(rkey, self.ttl, data_str)
        except Exception:
            pass

    def _delete_redis(self, sid: str) -> None:
        rkey = self._redis_key(sid)
        try:
            self._redis.delete(rkey)
        except Exception:
            pass
