import pytest

from asok.core import Asok
from asok.exceptions import SecurityError
from asok.request import Request


def test_static_directory_traversal(tmp_path):
    """Verify that path traversal in static file serving is blocked with 403."""
    # Create a temporary directory structure
    partials_dir = tmp_path / "src" / "partials"
    partials_dir.mkdir(parents=True)

    # Create a css folder and a css file
    css_dir = partials_dir / "css"
    css_dir.mkdir()
    css_file = css_dir / "style.css"
    css_file.write_text("body { color: red; }")

    # Create a file in the parent folder (forbidden)
    forbidden_file = partials_dir / "secret.txt"
    forbidden_file.write_text("secret content")

    app = Asok()
    app.root_dir = str(tmp_path)
    app._init_paths_and_extensions()

    # Mock environment and request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/css/../secret.txt",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)

    responses = []

    def start_response(status, headers):
        responses.append((status, headers))

    res = app._handle_static_request(request, environ, start_response)

    # Must return a response list, and start_response must have been called with 403 Forbidden
    assert res is not None
    assert len(responses) == 1
    assert responses[0][0] == "403 Forbidden"
    assert b"secret content" not in res[0]


def test_static_valid_request(tmp_path):
    """Verify that a normal valid request to static files succeeds."""
    partials_dir = tmp_path / "src" / "partials"
    partials_dir.mkdir(parents=True)
    css_dir = partials_dir / "css"
    css_dir.mkdir()
    css_file = css_dir / "style.css"
    css_file.write_text("body { color: red; }")

    app = Asok()
    app.root_dir = str(tmp_path)
    app._init_paths_and_extensions()

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/css/style.css",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)

    responses = []

    def start_response(status, headers):
        responses.append((status, headers))

    res = app._handle_static_request(request, environ, start_response)
    assert res is not None
    assert len(responses) == 1
    assert responses[0][0] == "200 OK"
    assert b"body { color: red; }" in res[0]


def test_csrf_port_independence():
    """Verify that CSRF validation succeeds even if Origin/Referer has a custom port."""
    app = Asok()
    app.config["SECRET_KEY"] = "test-secret"

    # Host header with no port, Origin with standard or non-standard port
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/submit",
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "example.com",
        "HTTP_ORIGIN": "https://example.com:8443",
        "asok.app": app,
    }
    request = Request(environ)
    request.csrf_token_value = "token123"
    request.form["csrf_token"] = "token123"

    # This should NOT raise SecurityError
    try:
        request.verify_csrf()
    except SecurityError as e:
        pytest.fail(f"CSRF validation failed with port in Origin: {e}")

    # Host header with port, Origin with different port
    environ2 = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/submit",
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "example.com:8080",
        "HTTP_ORIGIN": "https://example.com:8443",
        "asok.app": app,
    }
    request2 = Request(environ2)
    request2.csrf_token_value = "token123"
    request2.form["csrf_token"] = "token123"

    # This should NOT raise SecurityError
    try:
        request2.verify_csrf()
    except SecurityError as e:
        pytest.fail(f"CSRF validation failed with different ports in Host/Origin: {e}")


def test_csrf_mismatched_domain():
    """Verify that CSRF validation still fails if the domain itself is mismatched."""
    app = Asok()
    app.config["SECRET_KEY"] = "test-secret"

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/submit",
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "example.com",
        "HTTP_ORIGIN": "https://evil.com:8443",
        "asok.app": app,
    }
    request = Request(environ)
    request.csrf_token_value = "token123"
    request.form["csrf_token"] = "token123"

    with pytest.raises(SecurityError) as exc_info:
        request.verify_csrf()
    assert "CSRF Origin mismatch" in str(exc_info.value)
