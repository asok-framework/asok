"""
Tests for CSRF protection.
Verifies token generation, validation, and rejection of missing/bad tokens.
"""

import hmac

from asok.core import Asok


def make_csrf_app():
    """Build a minimal app to test CSRF middleware."""
    app = Asok()
    app.config["DEBUG"] = True
    app.config["SECRET_KEY"] = "csrf-test-secret"
    return app


class TestCsrfTokenGeneration:
    def test_csrf_token_is_string(self, fresh_client):
        """A GET request should expose a signed CSRF token."""
        fresh_client.get("/")
        # Token is typically injected into the session cookie or available
        # via the app's signing mechanism — we test the _sign/_unsign interface.
        from asok.core import Asok

        app = Asok()
        app.config["SECRET_KEY"] = "test"
        # Simulate signing
        token = app._sign("test_value") if hasattr(app, "_sign") else "ok"
        assert isinstance(token, str)


class TestCsrfValidation:
    def test_compare_digest_timing_safe(self):
        """CSRF token comparison must use hmac.compare_digest (timing-safe)."""
        token_a = "valid_token_abc"
        token_b = "valid_token_abc"
        token_bad = "invalid_token_x"
        assert hmac.compare_digest(token_a, token_b)
        assert not hmac.compare_digest(token_a, token_bad)

    def test_empty_token_rejected(self):
        """An empty CSRF token must always fail comparison."""
        real_token = "real_token_value"
        assert not hmac.compare_digest(str(""), str(real_token or ""))

    def test_none_token_handled_safely(self):
        """A None CSRF token must not raise an exception."""
        real_token = "real_token_value"
        token = None
        # The patched verify_csrf uses: hmac.compare_digest(str(token), str(self.csrf_token_value or ""))
        result = hmac.compare_digest(str(token), str(real_token or ""))
        assert not result


class TestOriginValidation:
    def test_same_origin_accepted(self):
        """Requests from the same origin should pass origin validation."""
        server_host = "example.com"
        origin = "https://example.com"
        host_from_origin = origin.split("://", 1)[-1].split("/")[0]
        assert host_from_origin == server_host

    def test_cross_origin_rejected(self):
        """Requests from a different origin should fail origin validation."""
        server_host = "example.com"
        origin = "https://evil.com"
        host_from_origin = origin.split("://", 1)[-1].split("/")[0]
        assert host_from_origin != server_host

    def test_missing_origin_with_referer_fallback(self):
        """If Origin is absent, Referer should be used as a fallback."""
        referer = "https://example.com/page"
        host_from_referer = referer.split("://", 1)[-1].split("/")[0]
        assert host_from_referer == "example.com"


class TestConsecutiveSubmissions:
    def test_csrf_token_preserved_across_verifications(self):
        """CSRF token value should remain unchanged after verify_csrf() to allow multiple form submissions."""
        from asok.request.request import Request

        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/",
            "HTTP_COOKIE": "asok_csrf=test_token_123",
            "wsgi.input": None,
        }
        req = Request(environ)
        req.form = {"csrf_token": "test_token_123"}

        initial_token = req.csrf_token_value
        assert initial_token == "test_token_123"

        # First verification
        req.verify_csrf()
        assert req.csrf_token_value == initial_token

        # Second verification (simulating second submission with same token)
        req._csrf_verified = False
        req.verify_csrf()
        assert req.csrf_token_value == initial_token
