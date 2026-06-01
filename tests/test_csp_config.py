"""Tests for configurable Content Security Policy."""

import pytest

from asok import Asok


def test_default_csp():
    """Test that default CSP is applied without custom config."""
    app = Asok()
    headers = dict(app._security_headers())

    assert "Content-Security-Policy" in headers
    csp = headers["Content-Security-Policy"]

    # Check default directives
    assert "default-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    # SECURITY: unsafe-eval and unsafe-inline were removed from script-src for security
    assert "script-src 'self'" in csp
    # Verify that script-src does NOT contain unsafe-eval or unsafe-inline
    assert "script-src 'self' 'unsafe-eval'" not in csp
    assert "script-src 'self' 'unsafe-inline'" not in csp


def test_csp_extend_style_src():
    """Test extending style-src to allow Google Fonts."""
    app = Asok()
    app.config["CSP"] = {"style-src": ["https://fonts.googleapis.com"]}
    headers = dict(app._security_headers())
    csp = headers["Content-Security-Policy"]

    # Should include both default and custom values
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp


def test_csp_add_font_src():
    """Test adding a new font-src directive."""
    app = Asok()
    app.config["CSP"] = {"font-src": ["'self'", "https://fonts.gstatic.com"]}
    headers = dict(app._security_headers())
    csp = headers["Content-Security-Policy"]

    # Should include the new directive
    assert "font-src 'self' https://fonts.gstatic.com" in csp


def test_csp_google_fonts_full():
    """Test complete Google Fonts integration."""
    app = Asok()
    app.config["CSP"] = {
        "style-src": ["https://fonts.googleapis.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
    }
    headers = dict(app._security_headers())
    csp = headers["Content-Security-Policy"]

    # Check both directives
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "font-src 'self' https://fonts.gstatic.com" in csp


def test_csp_single_string_value():
    """Test that single string values work (not just lists)."""
    app = Asok()
    app.config["CSP"] = {"img-src": "https://example.com"}
    headers = dict(app._security_headers())
    csp = headers["Content-Security-Policy"]

    # img-src now has default values ('self', 'data:' and 'blob:' for image previews)
    assert "img-src 'self' data: blob: https://example.com" in csp


def test_csp_multiple_custom_directives():
    """Test adding multiple custom directives."""
    app = Asok()
    app.config["CSP"] = {
        "style-src": ["https://fonts.googleapis.com"],
        "font-src": ["'self'", "https://fonts.gstatic.com"],
        "img-src": ["'self'", "data:", "blob:", "https://example.com"],
    }
    headers = dict(app._security_headers())
    csp = headers["Content-Security-Policy"]

    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "font-src 'self' https://fonts.gstatic.com" in csp
    assert "img-src 'self' data: blob: https://example.com" in csp


def test_csp_with_nonce():
    """Test that nonce-based script-src works with custom CSP."""
    app = Asok()
    app.config["CSP"] = {"style-src": ["https://fonts.googleapis.com"]}
    headers = dict(app._security_headers(nonce="abc123"))
    csp = headers["Content-Security-Policy"]

    # Should have nonce in script-src
    assert "'nonce-abc123'" in csp
    # Should NOT have nonce in style-src (to keep unsafe-inline working for attributes)
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "'nonce-abc123'" not in csp.split("style-src")[1].split(";")[0]


def test_security_headers_disabled():
    """Test that CSP is not added when security headers are disabled."""
    app = Asok()
    app.config["SECURITY_HEADERS"] = False
    headers = dict(app._security_headers())

    assert "Content-Security-Policy" not in headers


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
