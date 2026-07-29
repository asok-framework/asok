"""
Session management system for the Asok framework.

Provides the dictionary-like Session object and SessionStore implementations
(in-memory and Redis-backed) to manage client state.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from operator import itemgetter
from typing import Any, Optional


class Session(dict):
    """A dictionary-like object representing a user session that tracks its own modification state."""

    def __init__(self, *args: Any, **kwargs: Any):
        """Initialize the Session instance and track modification status."""
        super().__init__(*args, **kwargs)
        self.modified: bool = False
        self.sid: Optional[str] = None

    def __setitem__(self, key: Any, value: Any) -> None:
        """Set an item in the session and mark the session as modified."""
        self.modified = True
        super().__setitem__(key, value)

    def __delitem__(self, key: Any) -> None:
        """Delete an item from the session and mark the session as modified."""
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

    def setdefault(self, key: Any, default: Any = None) -> Any:
        """Insert key with a value of default if key is not in the dictionary.
        Marks the session as modified if key was not present.
        """
        if key not in self:
            self.modified = True
        return super().setdefault(key, default)


def _safe_json_size(v: Any) -> int:
    try:
        return len(json.dumps(v))
    except Exception:
        return 999999


def _find_largest_key(data: dict[str, Any], protect: str) -> str | None:
    keys = list(data.keys())
    if len(keys) > 1 and protect in keys:
        keys.remove(protect)

    largest_key = None
    largest_size = -1
    for k in keys:
        v_size = _safe_json_size(data[k])
        if v_size > largest_size:
            largest_size = v_size
            largest_key = k
    return largest_key


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
        self._memory: OrderedDict[str, dict[str, Any]] = (
            OrderedDict()
        )  # sid -> {"data": dict, "ts": float}

        if backend == "file":
            self._init_file_backend()
        elif backend == "redis":
            self._init_redis_backend()

    def _init_file_backend(self) -> None:
        os.makedirs(self.path, exist_ok=True)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass

    def _init_redis_backend(self) -> None:
        try:
            import redis
        except ImportError:
            raise ImportError(
                "The 'redis' library is required to use the Redis session backend. "
                "Install it using 'pip install asok[redis]'."
            )
        redis_url = (
            os.environ.get("ASOK_REDIS_URL")
            or os.environ.get("REDIS_URL")
            or "redis://localhost:6379/0"
        )
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

    def delete(self, sid: str) -> bool:
        """Remove a session from storage. Returns True if deleted, False otherwise."""
        if self.backend == "file":
            return self._delete_file(sid)
        elif self.backend == "redis":
            return self._delete_redis(sid)
        return self._delete_memory(sid)

    def get_user_sessions(self, user_id: Any) -> list[dict[str, Any]]:
        """Return all active, non-expired sessions for a specific user ID."""
        if not user_id:
            return []
        user_id_str = str(user_id)

        if self.backend == "file":
            return self._get_user_sessions_file(user_id_str)
        elif self.backend == "redis":
            return self._get_user_sessions_redis(user_id_str)
        return self._get_user_sessions_memory(user_id_str)

    @staticmethod
    def _matches_user_id(data: dict, user_id_str: str) -> bool:
        uid = data.get("user_id") or data.get("_user_id")
        return str(uid) == user_id_str if uid is not None else False

    @staticmethod
    def _session_entry_if_match(
        sid: str, data: dict, ts: float, user_id_str: str
    ) -> "dict[str, Any] | None":
        if not SessionStore._matches_user_id(data, user_id_str):
            return None
        if data.get("_revoked") or data.get("_logged_out"):
            return None
        return {
            "sid": sid,
            "ip": data.get("_ip", "Unknown"),
            "user_agent": data.get("_user_agent", "Unknown"),
            "last_seen": data.get("_last_seen", ts),
            "created_at": data.get("_created_at", ts),
        }

    def _try_load_file_session(self, fname: str, user_id_str: str) -> "dict[str, Any] | None":
        sid = fname[:-5]
        fpath = os.path.join(self.path, fname)
        try:
            with open(fpath, "r") as f:
                entry = json.load(f)
            ts = entry.get("ts", 0)
            if time.time() - ts > self.ttl:
                return None
            return self._session_entry_if_match(sid, entry.get("data", {}), ts, user_id_str)
        except Exception:
            return None

    @staticmethod
    def _decode_redis_sid(key: Any) -> str:
        raw = key.decode("utf-8") if isinstance(key, bytes) else key
        return raw.split(":", 1)[1]

    def _try_load_redis_session(self, key: Any, user_id_str: str) -> "dict[str, Any] | None":
        val = self._redis.get(key)
        if not val:
            return None
        raw = val.decode("utf-8") if isinstance(val, bytes) else val
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return self._session_entry_if_match(self._decode_redis_sid(key), data, time.time(), user_id_str)

    def _get_user_sessions_memory(self, user_id_str: str) -> list[dict[str, Any]]:
        now = time.time()
        sessions = []
        with self._lock:
            for sid, entry in list(self._memory.items()):
                if now - entry["ts"] > self.ttl:
                    continue
                hit = self._session_entry_if_match(sid, entry.get("data", {}), entry["ts"], user_id_str)
                if hit:
                    sessions.append(hit)
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def _get_user_sessions_file(self, user_id_str: str) -> list[dict[str, Any]]:
        if not os.path.isdir(self.path):
            return []
        sessions = []
        for fname in os.listdir(self.path):
            if not fname.endswith(".json"):
                continue
            hit = self._try_load_file_session(fname, user_id_str)
            if hit:
                sessions.append(hit)
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def _get_user_sessions_redis(self, user_id_str: str) -> list[dict[str, Any]]:
        sessions = []
        try:
            for key in self._redis.scan_iter("session:*"):
                hit = self._try_load_redis_session(key, user_id_str)
                if hit:
                    sessions.append(hit)
        except Exception:
            pass
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def revoke_user_session(self, user_id: Any, sid: str) -> bool:
        """Revoke a session if it belongs to user_id."""
        data = self.load(sid)
        if not data:
            return False
        uid = data.get("user_id") or data.get("_user_id")
        if uid is not None and str(uid) == str(user_id):
            self.delete(sid)
            return True
        return False

    def revoke_all_user_sessions(self, user_id: Any) -> int:
        """Revoke all active sessions for a given user_id."""
        sessions = self.get_user_sessions(user_id)
        count = 0
        for s in sessions:
            if self.delete(s["sid"]):
                count += 1
        return count

    @staticmethod
    def _session_entry_any(sid: str, data: dict, ts: float) -> "dict[str, Any] | None":
        uid = data.get("user_id") or data.get("_user_id")
        if uid is None:
            return None
        is_revoked = bool(data.get("_revoked") or data.get("_logged_out"))
        return {
            "sid": sid,
            "user_id": uid,
            "ip": data.get("_ip", "Unknown"),
            "user_agent": data.get("_user_agent", "Unknown"),
            "last_seen": data.get("_last_seen", ts),
            "created_at": data.get("_created_at", ts),
            "is_active": not is_revoked,
        }

    def get_all_active_sessions(self, max_age: Optional[float] = None) -> list[dict[str, Any]]:
        """Return sessions for any authenticated user within max_age seconds.

        max_age defaults to self.ttl. Pass a larger value (e.g. 86400 * 30) to
        include recently-expired sessions in admin listing views.
        """
        window = max_age if max_age is not None else self.ttl
        if self.backend == "file":
            return self._get_all_sessions_file(window)
        elif self.backend == "redis":
            return self._get_all_sessions_redis()
        return self._get_all_sessions_memory(window)

    def _get_all_sessions_memory(self, max_age: float) -> list[dict[str, Any]]:
        now = time.time()
        sessions = []
        with self._lock:
            for sid, entry in list(self._memory.items()):
                if now - entry["ts"] > max_age:
                    continue
                hit = self._session_entry_any(sid, entry.get("data", {}), entry["ts"])
                if hit:
                    sessions.append(hit)
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def _get_all_sessions_file(self, max_age: float) -> list[dict[str, Any]]:
        if not os.path.isdir(self.path):
            return []
        sessions = []
        now = time.time()
        for fname in os.listdir(self.path):
            if not fname.endswith(".json"):
                continue
            hit = self._load_file_session_any(fname, now, max_age)
            if hit:
                sessions.append(hit)
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def _load_file_session_any(
        self, fname: str, now: float, max_age: float
    ) -> "dict[str, Any] | None":
        sid = fname[:-5]
        fpath = os.path.join(self.path, fname)
        try:
            with open(fpath, "r") as f:
                entry = json.load(f)
            ts = entry.get("ts", 0)
            if now - ts > max_age:
                return None
            return self._session_entry_any(sid, entry.get("data", {}), ts)
        except Exception:
            return None

    def _get_all_sessions_redis(self) -> list[dict[str, Any]]:
        sessions = []
        now = time.time()
        try:
            for key in self._redis.scan_iter("session:*"):
                hit = self._load_redis_session_any(key, now)
                if hit:
                    sessions.append(hit)
        except Exception:
            pass
        sessions.sort(key=itemgetter("last_seen"), reverse=True)
        return sessions

    def _load_redis_session_any(self, key: Any, now: float) -> "dict[str, Any] | None":
        val = self._redis.get(key)
        if not val:
            return None
        raw = val.decode("utf-8") if isinstance(val, bytes) else val
        try:
            data = json.loads(raw)
            sid = self._decode_redis_sid(key)
            return self._session_entry_any(sid, data, now)
        except Exception:
            return None

    def generate_sid(self) -> str:
        """Generate a new unique session identifier using a secure cryptographically strong RNG."""
        return secrets.token_hex(32)

    def regenerate(self, sid: str) -> str:
        """Rotate the session ID while preserving data to prevent session fixation.

        Returns the new session ID.

        SECURITY: Atomic operation to prevent race conditions during session rotation.
        """
        data = self.load(sid)
        new_sid = self.generate_sid()

        if data is not None:
            # SECURITY: Use backend-specific atomic operations to prevent race conditions
            if self.backend == "memory":
                # Memory backend: use lock for atomicity
                with self._lock:
                    self._memory[new_sid] = {"data": dict(data), "ts": time.time()}
                    self._memory.pop(sid, None)
            elif self.backend == "redis":
                # Redis backend: use pipeline for atomic operations
                try:
                    pipeline = self._redis.pipeline()
                    rkey_old = self._redis_key(sid)
                    rkey_new = self._redis_key(new_sid)
                    data_str = json.dumps(data)
                    pipeline.setex(rkey_new, self.ttl, data_str)
                    pipeline.delete(rkey_old)
                    pipeline.execute()
                except Exception:
                    # Fallback to non-atomic if pipeline fails
                    self.save(new_sid, data)
                    self.delete(sid)
            else:
                # File backend: use try/finally for cleanup
                try:
                    self.save(new_sid, data)
                finally:
                    self.delete(sid)
        else:
            # No data to preserve, just delete old session
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

    def _clean_single_file(self, fpath: str, now: float) -> bool:
        try:
            with open(fpath, "r") as f:
                entry = json.load(f)
            if now - entry.get("ts", 0) > self.ttl:
                os.remove(fpath)
                return True
        except (json.JSONDecodeError, OSError, KeyError):
            pass
        return False

    def _cleanup_file(self) -> int:
        count = 0
        if not os.path.isdir(self.path):
            return 0
        now = time.time()
        for fname in os.listdir(self.path):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(self.path, fname)
            if self._clean_single_file(fpath, now):
                count += 1
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

    def _truncate_if_large(self, data: dict[str, Any]) -> dict[str, Any]:
        return self._perform_truncation_check(data)

    def _perform_truncation_check(self, data: dict[str, Any]) -> dict[str, Any]:
        try:
            data_str = json.dumps(data)
            if len(data_str) > 100_000:
                return self._do_truncate_session(data, len(data_str))
        except (TypeError, ValueError):
            pass
        return data

    def _do_truncate_session(self, data: dict[str, Any], size: int) -> dict[str, Any]:
        import logging

        logging.getLogger("asok.session").warning(
            "Session data too large (%d bytes), truncating to fit 100KB limit", size
        )
        if not isinstance(data, dict):
            return data

        pruned = dict(data)
        while len(pruned) > 0 and len(json.dumps(pruned)) > 100_000:
            largest_key = _find_largest_key(pruned, "_user_id")
            if largest_key is not None:
                del pruned[largest_key]
            else:
                pruned.clear()
                break

        return pruned

    def _purge_expired_memory(self, now: float) -> None:
        expired = [k for k, v in self._memory.items() if now - v["ts"] > self.ttl]
        for k in expired:
            del self._memory[k]

    def _evict_excess_memory_sessions(self) -> None:
        if len(self._memory) > self.max_sessions:
            self._purge_expired_memory(time.time())
            while len(self._memory) > self.max_sessions:
                self._memory.popitem(last=False)

    def _save_memory(self, sid, data):
        """Save session data to memory.

        SECURITY: Session data size limits prevent DoS.
        """
        data = self._truncate_if_large(data)
        with self._lock:
            if sid in self._memory:
                self._memory.move_to_end(sid)
            self._memory[sid] = {"data": dict(data), "ts": time.time()}
            # Evict oldest sessions if over capacity
            self._evict_excess_memory_sessions()

    def _delete_memory(self, sid: str) -> bool:
        with self._lock:
            return self._memory.pop(sid, None) is not None

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

    def _write_session_file(self, fpath: str, entry: dict[str, Any]) -> None:
        # Write to a temp file then os.replace() so a crash mid-write can never
        # leave a truncated JSON file (which would silently drop the session).
        tmp_path = f"{fpath}.tmp"
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(entry, f)
            os.replace(tmp_path, fpath)
        except OSError as e:
            self._write_session_file_fallback(fpath, tmp_path, entry, e)

    def _write_session_file_fallback(
        self, fpath: str, tmp_path: str, entry: dict[str, Any], err: OSError
    ) -> None:
        # Fallback for systems that don't support os.open securely
        import logging

        logger = logging.getLogger("asok.session")
        logger.warning(f"Failed to create session file with secure permissions: {err}")
        try:
            os.remove(tmp_path)
        except OSError:
            pass

        with open(fpath, "w") as f:
            json.dump(entry, f)
        try:
            os.chmod(fpath, 0o600)
        except OSError as chmod_err:
            # SECURITY: Log if chmod fails - session file may be world-readable!
            logger.error(
                f"SECURITY WARNING: Failed to set secure permissions (0600) on session file {fpath}: {chmod_err}. "
                "Session data may be exposed to other users on the system!"
            )

    def _save_file(self, sid, data):
        """Save session data to file.

        SECURITY: Session data size limits prevent DoS.
        """
        data = self._truncate_if_large(data)
        fpath = self._session_file(sid)
        self._write_session_file(fpath, {"data": dict(data), "ts": time.time()})

    def _delete_file(self, sid: str) -> bool:
        fpath = self._session_file(sid)
        try:
            if os.path.isfile(fpath):
                os.remove(fpath)
                return True
        except OSError:
            pass
        return False

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
        data = self._truncate_if_large(data)
        try:
            data_str = json.dumps(data)
        except (TypeError, ValueError):
            return

        rkey = self._redis_key(sid)
        try:
            self._redis.setex(rkey, self.ttl, data_str)
        except Exception:
            pass

    def _delete_redis(self, sid: str) -> bool:
        rkey = self._redis_key(sid)
        try:
            return bool(self._redis.delete(rkey))
        except Exception:
            return False
