"""
Tests for the session module.
Covers: SessionStore (memory + file backends), SID generation, save/load/delete,
TTL expiry, Session dict interface.
"""

import time

import pytest

from asok.session import Session, SessionStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mem_store():
    """In-memory session store."""
    return SessionStore(backend="memory", ttl=3600)


@pytest.fixture
def file_store(tmp_path):
    """File-based session store backed by a temp directory."""
    return SessionStore(backend="file", path=str(tmp_path / "sessions"), ttl=3600)


# ---------------------------------------------------------------------------
# SID generation
# ---------------------------------------------------------------------------


class TestSidGeneration:
    def test_sid_is_string(self, mem_store):
        sid = mem_store.generate_sid()
        assert isinstance(sid, str)

    def test_sid_is_hex(self, mem_store):
        sid = mem_store.generate_sid()
        int(sid, 16)  # Should not raise

    def test_sid_is_long_enough(self, mem_store):
        """Session IDs should be at least 32 chars (128 bits of entropy)."""
        sid = mem_store.generate_sid()
        assert len(sid) >= 32

    def test_each_sid_is_unique(self, mem_store):
        sids = {mem_store.generate_sid() for _ in range(100)}
        assert len(sids) == 100


# ---------------------------------------------------------------------------
# Memory backend: save / load / delete
# ---------------------------------------------------------------------------


class TestMemoryStore:
    def test_save_and_load(self, mem_store):
        sid = mem_store.generate_sid()
        mem_store.save(sid, {"user_id": 1, "name": "Alice"})
        data = mem_store.load(sid)
        assert data == {"user_id": 1, "name": "Alice"}

    def test_load_missing_returns_none(self, mem_store):
        assert mem_store.load("nonexistent-sid-xyz") is None

    def test_delete_removes_session(self, mem_store):
        sid = mem_store.generate_sid()
        mem_store.save(sid, {"x": 1})
        mem_store.delete(sid)
        assert mem_store.load(sid) is None

    def test_overwrite_session(self, mem_store):
        sid = mem_store.generate_sid()
        mem_store.save(sid, {"step": 1})
        mem_store.save(sid, {"step": 2})
        assert mem_store.load(sid) == {"step": 2}

    def test_stores_complex_data(self, mem_store):
        sid = mem_store.generate_sid()
        data = {"user": {"id": 42, "roles": ["admin", "editor"]}, "cart": [1, 2, 3]}
        mem_store.save(sid, data)
        assert mem_store.load(sid) == data

    def test_stores_empty_dict(self, mem_store):
        sid = mem_store.generate_sid()
        mem_store.save(sid, {})
        assert mem_store.load(sid) == {}

    def test_ttl_expiry(self):
        """Session should expire after TTL seconds."""
        store = SessionStore(backend="memory", ttl=1)
        sid = store.generate_sid()
        store.save(sid, {"alive": True})
        assert store.load(sid) is not None
        time.sleep(1.2)
        assert store.load(sid) is None


# ---------------------------------------------------------------------------
# File backend: save / load / delete
# ---------------------------------------------------------------------------


class TestFileStore:
    def test_save_and_load(self, file_store):
        sid = file_store.generate_sid()
        file_store.save(sid, {"user_id": 99})
        assert file_store.load(sid) == {"user_id": 99}

    def test_load_missing_returns_none(self, file_store):
        assert file_store.load("a" * 64) is None

    def test_delete_removes_session(self, file_store):
        sid = file_store.generate_sid()
        file_store.save(sid, {"x": 1})
        file_store.delete(sid)
        assert file_store.load(sid) is None

    def test_persists_complex_types(self, file_store):
        sid = file_store.generate_sid()
        data = {"nested": {"a": [1, 2, 3]}, "flag": True}
        file_store.save(sid, data)
        assert file_store.load(sid) == data


# ---------------------------------------------------------------------------
# Session dict interface
# ---------------------------------------------------------------------------


class TestSessionDict:
    def test_session_is_dict_like(self):
        s = Session(user_id=1, name="Alice")
        assert s["user_id"] == 1
        assert s.get("name") == "Alice"

    def test_session_update(self):
        s = Session()
        s.update({"role": "admin", "lang": "fr"})
        assert s["role"] == "admin"
        assert s["lang"] == "fr"

    def test_session_clear(self):
        s = Session(user_id=1)
        s.clear()
        assert len(s) == 0

    def test_session_pop(self):
        s = Session(user_id=1, name="Alice")
        val = s.pop("user_id")
        assert val == 1
        assert "user_id" not in s

    def test_session_get_default(self):
        s = Session()
        assert s.get("missing_key", "default") == "default"

    def test_session_setdefault(self):
        s = Session()
        s.setdefault("visits", 0)
        assert s["visits"] == 0
        s.setdefault("visits", 99)
        assert s["visits"] == 0  # Not overwritten

    def test_session_supports_iteration(self):
        s = Session(a=1, b=2)
        keys = list(s.keys())
        assert "a" in keys and "b" in keys
