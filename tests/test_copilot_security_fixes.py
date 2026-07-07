"""Tests for advanced Copilot security fixes.

Covers:
1. Replay session prevention via server-side session linked cookies.
2. CSRF Origin/Referer strict scheme and port verification.
3. CORS wildcard + credentials restrictions in production mode.
4. Conditional X-CSRF-Token exposure to prevent exfiltration.
"""

import pytest

from asok.core import Asok
from asok.exceptions import SecurityError
from asok.orm import Field, Model
from asok.request import Request
from asok.session import SessionStore
from asok.ws import WebSocketServer


class DummyApp:
    def __init__(self, root_dir=None):
        self.root_dir = root_dir or "."
        self.config = {
            "SECRET_KEY": "test_secret_key_for_signing_and_validation",
            "DATABASE": ":memory:",
            "AUTH_MODEL": "CopilotMockUser",
            "CORS_ORIGINS": "*",
            "DEBUG": False,
        }
        self.models = []
        self._session_store = SessionStore(backend="memory")

    def _sign(self, value):
        import hashlib
        import hmac

        key = self.config["SECRET_KEY"].encode()
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self, signed_value):
        import hmac

        if not signed_value or "." not in signed_value:
            return None
        val, sig = signed_value.rsplit(".", 1)
        if hmac.compare_digest(self._sign(val), signed_value):
            return val
        return None


class CopilotMockUser(Model):
    _db_path = ":memory:"
    __tablename__ = "copilot_mock_users"
    username = Field.String()
    password = Field.Password()


def test_session_revocation_on_replay():
    """Verify that stealing a cryptographically valid asok_session cookie fails once revoked server-side."""
    app = DummyApp()
    CopilotMockUser.create_table()
    user = CopilotMockUser.create(username="alice", password="password123")

    # Simulate login
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req = Request(environ)
    req.login(user)

    # Save session to store (normally handled by WSGI middleware at request end)
    app._session_store.save(req.session.sid, req.session)

    # Extract the cookie generated
    cookie_header = req.environ.get("asok.session_cookie", "")
    assert "asok_session=" in cookie_header

    # Extract cookie value from header
    cookie_val = cookie_header.split("asok_session=")[1].split(";")[0]

    # Verify session store contains user_id
    sid = req.session.sid
    sess_data = app._session_store.load(sid)
    assert sess_data is not None
    assert sess_data.get("user_id") == user.id

    # Create a new request presenting the stolen cookie
    environ2 = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "HTTP_COOKIE": f"asok_session={cookie_val}; asok_sid={app._sign(sid)}",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req2 = Request(environ2)
    # Stolen cookie should authorize the user
    assert req2.user is not None
    assert req2.user.id == user.id

    # Now, revoke the session (simulate logout or explicit revocation by clearing session)
    app._session_store.save(sid, {})  # clear session data

    environ3 = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "HTTP_COOKIE": f"asok_session={cookie_val}; asok_sid={app._sign(sid)}",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req3 = Request(environ3)
    # Stolen cookie must now be rejected because the server-side session is revoked
    assert req3.user is None


def test_websocket_server_stolen_cookie_revocation():
    """Verify WebSocket server rejects connections with stolen cookies once revoked server-side."""
    app = DummyApp()
    CopilotMockUser.create_table()
    user = CopilotMockUser.create(username="bob", password="password123")

    server = WebSocketServer(
        app=app, secret_key=app.config["SECRET_KEY"], auth_model="CopilotMockUser"
    )

    # Generate a valid session-linked cookie
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req = Request(environ)
    req.login(user)

    # Save session to store (normally handled by WSGI middleware at request end)
    app._session_store.save(req.session.sid, req.session)

    cookie_header = req.environ.get("asok.session_cookie", "")
    cookie_val = cookie_header.split("asok_session=")[1].split(";")[0]
    sid = req.session.sid

    # 1. Connection with active session should succeed
    headers = {"cookie": f"asok_session={cookie_val}"}
    resolved_user = server._resolve_user(headers)
    assert resolved_user is not None
    assert resolved_user.id == user.id

    # 2. Revoke the session
    app._session_store.save(sid, {})

    # 3. Connection with revoked session must fail
    resolved_user2 = server._resolve_user(headers)
    assert resolved_user2 is None


def test_strict_csrf_scheme_and_port():
    """Verify CSRF Origin/Referer strict verification blocks mismatches in scheme or port."""
    app = DummyApp()

    # Base HTTPS request on port 8000
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/submit",
        "HTTP_HOST": "localhost:8000",
        "wsgi.url_scheme": "https",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req = Request(environ)
    req.csrf_token_value = "token123"

    # Same Origin / Referer -> success
    req.headers["Origin"] = "https://localhost:8000"
    req.headers["X-CSRF-Token"] = "token123"
    req.verify_csrf()  # Should not raise SecurityError

    # Scheme mismatch -> failure
    req._csrf_verified = False
    req.headers["Origin"] = "http://localhost:8000"
    with pytest.raises(SecurityError) as exc:
        req.verify_csrf()
    assert "CSRF Origin mismatch" in str(exc.value)

    # Port mismatch -> success (port independence is verified here)
    req._csrf_verified = False
    req.csrf_token_value = "token123"
    req.headers["Origin"] = "https://localhost:8443"
    req.verify_csrf()  # Should not raise SecurityError

    # Port mismatch with STRICT_CSRF_PORT = True -> failure
    app.config["STRICT_CSRF_PORT"] = True
    req._csrf_verified = False
    req.csrf_token_value = "token123"
    req.headers["Origin"] = "https://localhost:8443"
    with pytest.raises(SecurityError) as exc:
        req.verify_csrf()
    assert "CSRF Port mismatch" in str(exc.value)

    # Restore config
    app.config["STRICT_CSRF_PORT"] = False

    # Host mismatch -> failure
    req._csrf_verified = False
    req.csrf_token_value = "token123"
    req.headers["Origin"] = "https://evil.com:8000"
    with pytest.raises(SecurityError) as exc:
        req.verify_csrf()
    assert "CSRF Origin mismatch" in str(exc.value)


def test_cors_credentials_wildcard_in_production():
    """Verify CORS wildcard denies credentials in production, but allows in dev/debug mode."""
    app = Asok()
    app.config["CORS_ORIGINS"] = "*"

    # 1. Production Mode (DEBUG=False)
    app.config["DEBUG"] = False

    headers = []
    environ = {"HTTP_ORIGIN": "https://external.com"}
    app._append_cors_headers(headers, environ)

    # Access-Control-Allow-Origin should be '*' in production with wildcard
    assert ("Access-Control-Allow-Origin", "*") in headers
    # Access-Control-Allow-Credentials must NOT be present
    assert ("Access-Control-Allow-Credentials", "true") not in headers

    # 2. Debug/Dev Mode (DEBUG=True)
    app2 = Asok()
    app2.config["CORS_ORIGINS"] = "*"
    app2.config["DEBUG"] = True

    headers2 = []
    app2._append_cors_headers(headers2, environ)

    # Access-Control-Allow-Origin should be the dynamic origin in dev mode
    assert ("Access-Control-Allow-Origin", "https://external.com") in headers2
    assert ("Access-Control-Allow-Credentials", "true") in headers2


def test_csrf_token_exposure_protection():
    """Verify X-CSRF-Token header is only exposed to Same-Origin and allowed CORS list."""
    app = Asok()
    app.config["CORS_ORIGINS"] = "*"
    app.config["DEBUG"] = False

    # 1. Same-Origin request (Origin header matches host)
    environ_same = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_ORIGIN": "https://localhost:8000",
        "wsgi.url_scheme": "https",
    }
    req_same = Request(environ_same)
    req_same.csrf_token_value = "secret123"

    headers_same = app._base_response_headers(req_same, environ_same)
    assert ("X-CSRF-Token", "secret123") in headers_same
    assert ("Access-Control-Expose-Headers", "X-CSRF-Token") in headers_same

    # 2. Unrestricted CORS Wildcard request (Origin is different, CORS is '*')
    environ_cross = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_ORIGIN": "https://evil.com",
        "wsgi.url_scheme": "https",
    }
    req_cross = Request(environ_cross)
    req_cross.csrf_token_value = "secret123"

    headers_cross = app._base_response_headers(req_cross, environ_cross)
    # Must NOT expose X-CSRF-Token to untrusted origins in wildcard mode
    assert ("X-CSRF-Token", "secret123") not in headers_cross
    assert ("Access-Control-Expose-Headers", "X-CSRF-Token") not in headers_cross

    # 3. Allow-listed CORS request (Origin in explicit list)
    app_trusted = Asok()
    app_trusted.config["CORS_ORIGINS"] = ["https://trusted.com"]
    app_trusted.config["DEBUG"] = False

    environ_trusted = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_ORIGIN": "https://trusted.com",
        "wsgi.url_scheme": "https",
    }
    req_trusted = Request(environ_trusted)
    req_trusted.csrf_token_value = "secret123"

    headers_trusted = app_trusted._base_response_headers(req_trusted, environ_trusted)
    # Should expose to explicitly trusted domains
    assert ("X-CSRF-Token", "secret123") in headers_trusted
    assert ("Access-Control-Expose-Headers", "X-CSRF-Token") in headers_trusted


def test_legacy_cookie_fallback_in_prod_and_dev():
    """Verify legacy cookie (without sid link) is rejected in production but accepted in dev."""
    app = DummyApp()
    CopilotMockUser.create_table()
    user = CopilotMockUser.create(username="charlie", password="password123")

    legacy_cookie = app._sign(str(user.id))

    # 1. Dev Mode (DEBUG = True)
    app.config["DEBUG"] = True
    environ_dev = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "HTTP_COOKIE": f"asok_session={legacy_cookie}",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req_dev = Request(environ_dev)
    assert req_dev.user is not None
    assert req_dev.user.id == user.id

    # 2. Prod Mode (DEBUG = False)
    app.config["DEBUG"] = False
    environ_prod = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "HTTP_COOKIE": f"asok_session={legacy_cookie}",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req_prod = Request(environ_prod)
    # Stolen legacy cookie must be rejected in production
    assert req_prod.user is None


def test_malformed_session_cookie_parsing_no_dos():
    """Verify that a malformed session cookie (non-numeric uid) does not crash the server (no ValueError)."""
    app = DummyApp()

    # Cookie value with ':' but non-numeric UID
    malformed_cookie = app._sign("abc:some-sid")

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "HTTP_COOKIE": f"asok_session={malformed_cookie}",
        "asok.app": app,
        "asok.secret_key": app.config["SECRET_KEY"],
    }
    req = Request(environ)
    # This should return None and NOT raise ValueError
    assert req.user is None
