"""
Tests for the custom exceptions module.
"""

from asok.exceptions import AbortException, RedirectException


class TestExceptions:
    def test_abort_exception_stores_status_and_message(self):
        exc = AbortException(404, "Page not found")
        assert exc.status == 404
        assert exc.message == "Page not found"

    def test_abort_exception_default_message(self):
        exc = AbortException(500)
        assert exc.status == 500
        assert exc.message is None

    def test_redirect_exception_stores_location_and_code(self):
        exc = RedirectException("/login", 301)
        assert exc.url == "/login"
        assert exc.status == 301

    def test_redirect_exception_default_code(self):
        exc = RedirectException("/dashboard")
        assert exc.url == "/dashboard"
        assert exc.status == 302


def test_debug_exception_page():
    from asok.core import Asok
    from asok.request import Request

    app = Asok()
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/crash-route",
        "asok.app": app,
    }
    req = Request(environ)
    req.params["test_param"] = "test_val"
    req.form["form_key"] = "form_val"

    try:
        raise ValueError("Crashed deliberately")
    except Exception as e:
        html = app._render_debug_exception_page(req, e)
        assert "ValueError" in html
        assert "Crashed deliberately" in html
        assert "test_param" in html
        assert "test_val" in html
        assert "form_key" in html
        assert "form_val" in html
