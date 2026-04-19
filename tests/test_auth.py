"""
Tests for the authentication module.
Covers: BearerToken (create/verify/expiry/tampering), MagicLink, HMAC signing.
"""

import io
import time

from asok.auth import BearerToken
from asok.request import Request

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SECRET = "test-secret-key-for-auth-tests"


def make_request(method="GET", path="/", data=None, headers=None):
    """Build a minimal Request with a configured secret key."""
    body = b""
    ct = ""
    if data:
        from urllib.parse import urlencode

        body = urlencode(data).encode()
        ct = "application/x-www-form-urlencoded"

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": ct,
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
        "asok.secret_key": SECRET,
    }
    for key, value in (headers or {}).items():
        wsgi_key = "HTTP_" + key.upper().replace("-", "_")
        environ[wsgi_key] = value

    return Request(environ)


# ---------------------------------------------------------------------------
# HMAC signing (core primitive used by all auth strategies)
# ---------------------------------------------------------------------------


class TestHmacSigning:
    def test_sign_returns_string(self):
        req = make_request()
        signed = req._sign("test_value")
        assert isinstance(signed, str)

    def test_sign_contains_original_value(self):
        req = make_request()
        signed = req._sign("hello")
        assert signed.startswith("hello.")

    def test_sign_produces_different_output_for_different_inputs(self):
        req = make_request()
        s1 = req._sign("value_a")
        s2 = req._sign("value_b")
        assert s1 != s2

    def test_unsign_verifies_correct_signature(self):
        req = make_request()
        signed = req._sign("user_42")
        unsigned = req._unsign(signed)
        assert unsigned == "user_42"

    def test_unsign_rejects_tampered_value(self):
        req = make_request()
        signed = req._sign("user_42")
        # Tamper with the payload
        tampered = "user_99." + signed.split(".", 1)[1]
        result = req._unsign(tampered)
        assert result is None

    def test_unsign_rejects_missing_signature(self):
        req = make_request()
        result = req._unsign("no_signature_here")
        assert result is None

    def test_unsign_rejects_empty_string(self):
        req = make_request()
        result = req._unsign("")
        assert result is None

    def test_different_secrets_produce_different_signatures(self):
        env1 = {**make_request().environ, "asok.secret_key": "secret_a"}
        env2 = {**make_request().environ, "asok.secret_key": "secret_b"}
        r1, r2 = Request(env1), Request(env2)
        s1 = r1._sign("payload")
        s2 = r2._sign("payload")
        assert s1 != s2

    def test_cross_secret_unsign_fails(self):
        env1 = {**make_request().environ, "asok.secret_key": "secret_a"}
        env2 = {**make_request().environ, "asok.secret_key": "secret_b"}
        r1, r2 = Request(env1), Request(env2)
        signed = r1._sign("payload")
        # r2 uses a different key — should reject r1's signature
        assert r2._unsign(signed) is None


# ---------------------------------------------------------------------------
# BearerToken
# ---------------------------------------------------------------------------


class TestBearerToken:
    def test_create_returns_string(self):
        req = make_request()
        token = BearerToken.create(req, user_id=1)
        assert isinstance(token, str)
        assert len(token) > 10

    def test_create_with_different_user_ids(self):
        req = make_request()
        t1 = BearerToken.create(req, user_id=1)
        t2 = BearerToken.create(req, user_id=2)
        assert t1 != t2

    def test_verify_valid_token(self):
        req = make_request()
        token = BearerToken.create(req, user_id=42)
        result = BearerToken.verify(req, token)
        assert result is not None
        # result should contain the user_id
        assert "42" in str(result)

    def test_verify_tampered_token_fails(self):
        req = make_request()
        token = BearerToken.create(req, user_id=1)
        tampered = "TAMPERED" + token[8:]
        result = BearerToken.verify(req, tampered)
        assert result is None

    def test_verify_empty_token_fails(self):
        req = make_request()
        result = BearerToken.verify(req, "")
        assert result is None

    def test_token_with_expiry(self):
        """A token with a future expiry should be valid."""
        req = make_request()
        token = BearerToken.create(req, user_id=99, expires_in=3600)
        result = BearerToken.verify(req, token)
        assert result is not None

    def test_expired_token_rejected(self):
        """A token with an already-past expiry must be rejected."""
        req = make_request()
        token = BearerToken.create(req, user_id=99, expires_in=1)
        time.sleep(1.2)
        result = BearerToken.verify(req, token)
        assert result is None

    def test_token_without_expiry_never_expires(self):
        """A token created without expires_in should not expire immediately."""
        req = make_request()
        token = BearerToken.create(req, user_id=7)
        result = BearerToken.verify(req, token)
        assert result is not None

    def test_cross_secret_token_rejected(self):
        """A token signed with one secret must be rejected by another."""
        env_a = {**make_request().environ, "asok.secret_key": "secret_a"}
        env_b = {**make_request().environ, "asok.secret_key": "secret_b"}
        req_a = Request(env_a)
        req_b = Request(env_b)
        token = BearerToken.create(req_a, user_id=1)
        result = BearerToken.verify(req_b, token)
        assert result is None
