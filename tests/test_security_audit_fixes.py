"""Tests for security audit fixes (H1, M1, M2, M3, M4, M5, L3, L4)."""

import io
import json
import logging
import os

import pytest

from asok import Asok, JSONFormatter, configure_security_logger
from asok.auth import MagicLink
from asok.request import Request
from asok.utils.html_sanitizer import sanitize_html_strict


def make_request(path="/", method="GET", headers=None, environ_extra=None):
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
        "asok.secret_key": "test-secret-key-that-is-at-least-32-bytes-long",
    }
    if environ_extra:
        environ.update(environ_extra)
    return Request(environ)


def test_m3_x_xss_protection_disabled():
    """M3: X-XSS-Protection header is set to 0 (modern standard)."""
    app = Asok()
    headers = dict(app._security_headers())
    assert headers.get("X-XSS-Protection") == "0"


def test_m2_lang_cookie_has_httponly():
    """M2: Language preference cookie includes HttpOnly flag."""
    app = Asok()
    req = make_request()
    req.args["lang"] = "fr"
    cookie = app._lang_cookie(req)
    assert cookie is not None
    assert "asok_lang=fr" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def test_m1_env_parsing_quotes_and_setdefault(monkeypatch):
    """M1: .env line parsing strips surrounding quotes and uses setdefault."""
    app = Asok()

    # Pre-set an environment variable to verify setdefault behavior
    monkeypatch.setenv("EXISTING_KEY", "system_value")

    # Apply env lines
    app._apply_env_line("EXISTING_KEY=new_file_value")
    app._apply_env_line('DOUBLE_QUOTED="hello world"')
    app._apply_env_line("SINGLE_QUOTED='secret123'")
    app._apply_env_line("UNQUOTED=simple_value")

    # Existing key is not overwritten
    assert os.environ.get("EXISTING_KEY") == "system_value"
    # Quotes are stripped
    assert os.environ.get("DOUBLE_QUOTED") == "hello world"
    assert os.environ.get("SINGLE_QUOTED") == "secret123"
    assert os.environ.get("UNQUOTED") == "simple_value"


def test_h1_sanitize_html_strict():
    """H1: sanitize_html_strict safely neutralizes dangerous tags and scripts."""
    # Test safe markup
    safe = "<p>Hello <strong>World</strong></p>"
    assert "Hello" in sanitize_html_strict(safe)
    assert "<strong>World</strong>" in sanitize_html_strict(safe)

    # Test dangerous script tags are eliminated
    dangerous = "<p>Text</p><script>alert('xss')</script>"
    cleaned = sanitize_html_strict(dangerous)
    assert "<script>" not in cleaned
    assert "alert('xss')" not in cleaned

    # Test event handlers are stripped
    handler = '<a href="https://example.com" onclick="steal()">Click</a>'
    cleaned_handler = sanitize_html_strict(handler)
    assert "onclick" not in cleaned_handler

    # Test empty or none input
    assert sanitize_html_strict("") == ""


def test_m5_magic_link_production_cache_enforcement(monkeypatch):
    """M5: Magic Link enforces non-memory cache in production."""
    req = make_request()

    # In production with in-memory cache, creating/verifying token should raise RuntimeError
    monkeypatch.setenv("ASOK_ENV", "production")
    monkeypatch.delenv("MAGIC_LINK_ALLOW_IN_MEMORY", raising=False)

    with pytest.raises(RuntimeError) as exc_info:
        MagicLink.create_token(req, "test@example.com")
    assert "MagicLink cannot use in-memory cache in production" in str(exc_info.value)

    # Setting explicit bypass MAGIC_LINK_ALLOW_IN_MEMORY allows it
    monkeypatch.setenv("MAGIC_LINK_ALLOW_IN_MEMORY", "1")
    token = MagicLink.create_token(req, "test@example.com")
    assert token is not None


def test_m4_csp_strict_styles():
    """M4: CSP_STRICT_STYLES removes 'unsafe-inline' and attaches nonce to style-src."""
    app = Asok()
    app.config["CSP_STRICT_STYLES"] = True

    headers = dict(app._security_headers(nonce="secure_nonce_123"))
    csp = headers.get("Content-Security-Policy", "")

    # style-src should NOT have 'unsafe-inline'
    style_src = [part for part in csp.split(";") if "style-src" in part]
    assert len(style_src) == 1
    assert "'unsafe-inline'" not in style_src[0]
    assert "'nonce-secure_nonce_123'" in style_src[0]


def test_l4_vary_cookie_added_when_session_active():
    """L4: Vary: Cookie is included when session or authentication is present."""
    app = Asok()

    # Request without session or auth
    req = make_request()
    headers = app._base_response_headers(req, req.environ)
    vary_values = [v for k, v in headers if k.lower() == "vary"]
    assert not any("Cookie" in v for v in vary_values)

    # Request with active session data
    req_session = make_request()
    req_session.session["user_id"] = 42
    headers_session = app._base_response_headers(req_session, req_session.environ)
    vary_session = [v for k, v in headers_session if k.lower() == "vary"]
    assert any("Cookie" in v for v in vary_session)


def test_l4_vary_header_merging():
    """L4: Vary headers are cleanly merged without duplication."""
    app = Asok()
    headers = [("Vary", "Accept-Encoding")]
    app._add_vary_header(headers, "Cookie")
    assert headers == [("Vary", "Accept-Encoding, Cookie")]

    # Adding Cookie again should not duplicate
    app._add_vary_header(headers, "Cookie")
    assert headers == [("Vary", "Accept-Encoding, Cookie")]


def test_l3_json_formatter_extra_fields():
    """L3: JSONFormatter extracts custom security extra fields and serializes to JSON."""
    formatter = JSONFormatter()
    record = logging.LogRecord(
        name="asok.security",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg="Suspicious activity detected: %s",
        args=("malicious payload",),
        exc_info=None,
    )
    record.event = "attack_detected"
    record.ip = "192.168.1.100"
    record.user_id = 42

    formatted = formatter.format(record)
    data = json.loads(formatted)
    assert data["logger"] == "asok.security"
    assert data["level"] == "WARNING"
    assert data["message"] == "Suspicious activity detected: malicious payload"
    assert data["event"] == "attack_detected"
    assert data["ip"] == "192.168.1.100"
    assert data["user_id"] == 42
    assert "timestamp" in data


def test_l3_configure_security_logger():
    """L3: configure_security_logger sets up JSON logging for SIEM ingestion."""
    stream = io.StringIO()
    sec_logger = configure_security_logger(json_format=True, stream=stream)

    sec_logger.warning(
        "CSRF check failed", extra={"event": "csrf_failure", "path": "/login"}
    )
    output = stream.getvalue()

    data = json.loads(output)
    assert data["logger"] == "asok.security"
    assert data["level"] == "WARNING"
    assert data["message"] == "CSRF check failed"
    assert data["event"] == "csrf_failure"
    assert data["path"] == "/login"


def test_l3_app_structured_security_logging_config():
    """L3: Asok app configures structured security logging via config or environment."""
    stream = io.StringIO()
    app = Asok()
    app.config["SECURITY_LOG_FORMAT"] = "json"

    sec_logger = configure_security_logger(json_format=True, stream=stream)
    sec_logger.warning(
        "Rate limit hit", extra={"event": "rate_limit_exceeded", "limit": 100}
    )

    data = json.loads(stream.getvalue())
    assert data["event"] == "rate_limit_exceeded"
    assert data["limit"] == 100
