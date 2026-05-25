"""Test that API static files (JS, CSS, SVG) are properly served."""

from asok import Asok
from asok.api import handle_docs_request
from asok.request import Request


def test_api_static_js_files():
    """Verify that JS files are served with correct content type."""
    app = Asok()
    app.config["SECRET_KEY"] = "test-key"

    # Test docs.min.js
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/asok-api/docs.min.js",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)
    response = handle_docs_request(app, request)

    assert response is not None, "docs.min.js should be served"
    assert request.content_type == "application/javascript"
    assert b"initApiDocs" in response, "Minified JS should contain initApiDocs function"


def test_api_static_css_files():
    """Verify that CSS files are served with correct content type."""
    app = Asok()

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/asok-api/docs.min.css",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)
    response = handle_docs_request(app, request)

    assert response is not None, "docs.min.css should be served"
    assert request.content_type == "text/css"


def test_api_static_svg_files():
    """Verify that SVG files are served with correct content type."""
    app = Asok()

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/asok-api/logo.svg",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)
    response = handle_docs_request(app, request)

    assert response is not None, "logo.svg should be served"
    assert request.content_type == "image/svg+xml"


def test_api_docs_template_references_minified_js():
    """Verify that the docs template references the minified JS file."""
    import os

    # Read the template directly
    current_dir = os.path.dirname(os.path.dirname(__file__))
    template_path = os.path.join(current_dir, "asok", "api", "templates", "docs.html")

    with open(template_path) as f:
        template_content = f.read()

    # Verify the template references docs.min.js (not docs.js)
    assert "/asok-api/docs.min.js" in template_content or "docs.min.js" in template_content
    assert "initApiDocs" in template_content, "Template should call initApiDocs function"
