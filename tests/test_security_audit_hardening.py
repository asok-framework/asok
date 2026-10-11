import io
import warnings

import pytest

from asok.admin.utils import _decrypt_totp_secret, _encrypt_totp_secret
from asok.core import Asok
from asok.request import Request
from asok.session import SessionStore
from asok.utils.security import is_safe_json_depth, safe_json_loads


def test_secret_key_length_warning_in_production():
    """Verify that using a SECRET_KEY shorter than 32 bytes emits a warning in production mode."""
    app = Asok.__new__(Asok)
    app.config = {"SECRET_KEY": "too-short-key", "DEBUG": False}

    with pytest.warns(UserWarning, match="SECRET_KEY is shorter than 32 bytes"):
        app._sign("test-value")

    # In DEBUG mode, no warning should be raised
    app.config["DEBUG"] = True
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        # Should not raise any warning
        signed = app._sign("test-value")
        assert signed is not None


def test_json_nesting_depth_detection():
    """Verify that is_safe_json_depth detects deeply nested payloads."""
    # Shallow json
    assert is_safe_json_depth('{"a": {"b": [1, 2, 3]}}', max_depth=10) is True

    # Nested brackets within max_depth
    valid_nested = "[" * 5 + "1" + "]" * 5
    assert is_safe_json_depth(valid_nested, max_depth=10) is True

    # Nested brackets exceeding max_depth
    deep_nested = "[" * 20 + "1" + "]" * 20
    assert is_safe_json_depth(deep_nested, max_depth=10) is False

    # Deeply nested objects exceeding max_depth
    deep_obj = '{"a":' * 15 + "1" + "}" * 15
    assert is_safe_json_depth(deep_obj, max_depth=10) is False

    # Deeply nested inside a string literal should NOT count towards depth
    string_with_brackets = '{"text": "' + ("[" * 50) + '"}'
    assert is_safe_json_depth(string_with_brackets, max_depth=10) is True


def test_safe_json_loads_rejects_deep_payload():
    """Verify safe_json_loads raises ValueError on deeply nested payloads."""
    deep_json = "[" * 105 + "1" + "]" * 105
    with pytest.raises(ValueError, match="JSON nesting depth exceeds maximum"):
        safe_json_loads(deep_json, max_depth=100)

    # Valid payload parses correctly
    normal_json = '{"user": {"name": "Alice", "roles": ["admin"]}}'
    parsed = safe_json_loads(normal_json, max_depth=100)
    assert parsed["user"]["name"] == "Alice"


def test_request_parses_json_with_depth_protection():
    """Verify Request ignores and logs payloads with excessive JSON depth without crashing."""
    deep_json = ("[" * 105 + "1" + "]" * 105).encode("utf-8")
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(deep_json)),
        "wsgi.input": io.BytesIO(deep_json),
    }
    req = Request(environ)
    assert req.json_body is None

    # Normal JSON parses properly
    valid_json = b'{"hello": "world"}'
    environ_valid = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(valid_json)),
        "wsgi.input": io.BytesIO(valid_json),
    }
    req_valid = Request(environ_valid)
    assert req_valid.json_body == {"hello": "world"}


def test_session_samesite_consistency():
    """Verify that both asok_sid and asok_session cookies use SESSION_SAMESITE."""
    app = Asok.__new__(Asok)
    app.config = {
        "SECRET_KEY": "a" * 32,
        "DEBUG": True,
        "SESSION_TTL": 3600,
        "SESSION_SAMESITE": "Strict",
    }
    app._session_store = SessionStore(backend="memory")

    # Create dummy session on request
    class DummySession(dict):
        def __init__(self):
            super().__init__()
            self.sid = "dummy_sid"
            self.modified = False

    environ = {
        "REQUEST_METHOD": "GET",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost:8000",
        "asok.app": app,
    }
    req = Request(environ)
    req._session = DummySession()

    # 1. Check asok_sid cookie with SameSite=Strict
    sid_cookie = app._session_sid_cookie(req)
    assert "SameSite=Strict" in sid_cookie

    # 2. Check asok_session cookie with SameSite=Strict
    session_cookie = req._session_cookie("test_value")
    assert "SameSite=Strict" in session_cookie

    # 3. Change to Lax and verify both reflect Lax
    app.config["SESSION_SAMESITE"] = "Lax"
    assert "SameSite=Lax" in app._session_sid_cookie(req)
    assert "SameSite=Lax" in req._session_cookie("test_value")

    # 4. Change to None and verify Secure is enforced
    app.config["SESSION_SAMESITE"] = "None"
    sid_cookie_none = app._session_sid_cookie(req)
    assert "SameSite=None" in sid_cookie_none
    assert "Secure" in sid_cookie_none

    sess_cookie_none = req._session_cookie("test_value")
    assert "SameSite=None" in sess_cookie_none
    assert "Secure" in sess_cookie_none


def test_totp_dedicated_encryption_key_support():
    """Verify that TOTP can use a dedicated TOTP_ENCRYPTION_KEY separate from SECRET_KEY."""
    secret = "JBSWY3DPEHPK3PXP"
    dedicated_key = "totp-dedicated-encryption-key-32"
    app_secret_key = "general-application-secret-key-32"

    # Encrypt with dedicated key
    encrypted = _encrypt_totp_secret(secret, dedicated_key)

    # Should decrypt with dedicated key
    assert _decrypt_totp_secret(encrypted, dedicated_key) == secret

    # Should NOT decrypt with app secret key
    assert _decrypt_totp_secret(encrypted, app_secret_key) is None
