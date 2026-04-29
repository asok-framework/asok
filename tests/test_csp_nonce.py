"""
Tests for Content Security Policy (CSP) nonce handling.
Ensures scripts have nonce attributes when strict-dynamic CSP is enabled.
"""

import io

from asok.request import Request


def make_request(path="/", method="GET"):
    """Build a minimal Request."""
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": "text/html",
        "CONTENT_LENGTH": "0",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
        "asok.secret_key": "test-secret",
    }
    return Request(environ)


def test_request_has_nonce():
    """Test that request has a nonce property."""
    req = make_request()

    # Nonce should be available
    nonce = req.nonce
    assert nonce is not None
    assert isinstance(nonce, str)
    assert len(nonce) > 0


def test_nonce_is_stable():
    """Test that nonce is stable across multiple accesses."""
    req = make_request()

    nonce1 = req.nonce
    nonce2 = req.nonce

    # Should be the same nonce
    assert nonce1 == nonce2


def test_nonce_format():
    """Test that nonce is base64-like (safe for CSP)."""
    req = make_request()
    nonce = req.nonce

    # Should be alphanumeric + / + _
    assert all(c.isalnum() or c in "/_-=" for c in nonce)


def test_different_requests_different_nonces():
    """Test that different requests get different nonces."""
    req1 = make_request()
    req2 = make_request()

    nonce1 = req1.nonce
    nonce2 = req2.nonce

    # Different requests should have different nonces
    assert nonce1 != nonce2
