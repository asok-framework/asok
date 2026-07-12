"""
Regression tests for the July 2026 audit hardening fixes:
- SQLite file: URIs opened with uri=True (no literal "file:..." file on disk)
- Atomic session/cache file writes (tmp + os.replace, no leftover .tmp)
- Single-use magic link tokens (replay is rejected)
- HSTS only sent when the request scheme is positively known to be HTTPS
"""

import hashlib
import hmac as _hmac
import io
import os

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECRET = "test-secret-key-for-hardening-tests"


class _MockApp:
    """Minimal app stub providing _sign/_unsign, matching SecurityMixin."""

    def __init__(self, secret: str):
        self._secret = secret.encode()
        self.config = {"SECRET_KEY": secret}

    def _sign(self, value):
        return f"{value}.{_hmac.new(self._secret, str(value).encode(), hashlib.sha256).hexdigest()}"

    def _unsign(self, signed_value):
        if not signed_value or "." not in signed_value:
            return None
        try:
            val, _sig = signed_value.rsplit(".", 1)
            if _hmac.compare_digest(self._sign(val), signed_value):
                return val
        except Exception:
            pass
        return None


def make_request():
    from asok.request import Request

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "QUERY_STRING": "",
        "CONTENT_TYPE": "",
        "CONTENT_LENGTH": "0",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
        "asok.app": _MockApp(SECRET),
    }
    return Request(environ)


# ---------------------------------------------------------------------------
# SQLite path handling
# ---------------------------------------------------------------------------


class TestSQLitePathHandling:
    def test_memory_db_creates_no_file(self, tmp_path, monkeypatch):
        """':memory:' must open an in-memory DB, not a file named ':memory:'."""
        from asok.orm.engines.sqlite import SQLiteEngine

        monkeypatch.chdir(tmp_path)
        engine = SQLiteEngine(":memory:")
        conn = engine.get_connection()
        conn.execute("CREATE TABLE t (id INTEGER)")
        assert not (tmp_path / ":memory:").exists()
        engine.close_connections()

    def test_file_uri_opened_as_uri(self, tmp_path, monkeypatch):
        """file: URIs must be passed with uri=True, not treated as literal filenames."""
        from asok.orm.engines.sqlite import SQLiteEngine

        monkeypatch.chdir(tmp_path)
        engine = SQLiteEngine("file:uri_test.db?mode=rwc")
        conn = engine.get_connection()
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        # The URI resolves to uri_test.db; no literal "file:uri_test.db?mode=rwc" file.
        assert (tmp_path / "uri_test.db").exists()
        assert not (tmp_path / "file:uri_test.db?mode=rwc").exists()
        engine.close_connections()


# ---------------------------------------------------------------------------
# Atomic file writes
# ---------------------------------------------------------------------------


class TestAtomicFileWrites:
    def test_session_file_write_roundtrip_no_tmp_leftover(self, tmp_path):
        from asok.session import SessionStore

        store = SessionStore(backend="file", path=str(tmp_path))
        sid = store.generate_sid()
        store.save(sid, {"user_id": 42})

        assert store.load(sid) == {"user_id": 42}
        leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert leftovers == []

    def test_cache_file_set_and_incr_no_tmp_leftover(self, tmp_path):
        from asok.cache import Cache

        cache = Cache(backend="file", path=str(tmp_path))
        cache.set("k", {"a": 1}, ttl=60)
        assert cache.get("k") == {"a": 1}
        assert cache.incr("counter", 1, ttl=60) == 1
        assert cache.incr("counter", 2) == 3

        leftovers = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
        assert leftovers == []


# ---------------------------------------------------------------------------
# Magic link single-use
# ---------------------------------------------------------------------------


class TestMagicLinkSingleUse:
    @pytest.fixture(autouse=True)
    def _clean_cache(self):
        from asok.cache import default_cache

        default_cache.flush()
        yield
        default_cache.flush()

    def test_token_verifies_once_then_rejected(self):
        from asok.auth import MagicLink

        req = make_request()
        token = MagicLink.create_token(req, "user@example.com", expires_in=3600)

        assert MagicLink.verify_token(req, token) == "user@example.com"
        # Replay must be rejected
        assert MagicLink.verify_token(req, token) is None

    def test_distinct_tokens_are_independent(self):
        from asok.auth import MagicLink

        req = make_request()
        t1 = MagicLink.create_token(req, "a@example.com", expires_in=3600)
        t2 = MagicLink.create_token(req, "b@example.com", expires_in=3600)

        assert MagicLink.verify_token(req, t1) == "a@example.com"
        # Consuming t1 must not consume t2
        assert MagicLink.verify_token(req, t2) == "b@example.com"


# ---------------------------------------------------------------------------
# HSTS scheme gating
# ---------------------------------------------------------------------------


class _RequestStub:
    def __init__(self, scheme: str):
        self.scheme = scheme
        self.host = "localhost"
        self.environ = {"SERVER_NAME": "localhost"}


class TestHstsSchemeGating:
    def _headers(self, request):
        from asok import Asok

        app = Asok()
        return [k for k, _ in app._security_headers(request=request)]

    def test_no_hsts_without_request(self):
        assert "Strict-Transport-Security" not in self._headers(None)

    def test_no_hsts_on_http(self):
        assert "Strict-Transport-Security" not in self._headers(_RequestStub("http"))

    def test_hsts_on_https(self):
        assert "Strict-Transport-Security" in self._headers(_RequestStub("https"))
