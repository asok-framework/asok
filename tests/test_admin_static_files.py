import pytest

from asok import Asok
from asok.admin import Admin
from asok.request import Request


def test_admin_static_font_serving():
    """Verify that local font files can be served via Admin._serve_static."""
    app = Asok()
    app.config["SECRET_KEY"] = "test-secret"
    admin = Admin(app)

    # Construct request to serve a font
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/static/fonts/inter-400.woff2",
        "wsgi.url_scheme": "http",
        "HTTP_HOST": "localhost",
    }
    request = Request(environ)

    # Dispatch the request to Admin
    # Note: admin.dispatch should handle requests starting with prefix + "/static/"
    try:
        admin.dispatch(request)
    except Exception as e:
        pytest.fail(f"Admin dispatch raised exception: {e}")

    # Check if a binary response was recorded in the environment
    binary_resp = request.environ.get("asok.binary_response")
    assert binary_resp is not None, "Font content should be set as binary response"
    assert len(binary_resp) > 0, "Font content should not be empty"
    assert request.content_type == "font/woff2", f"Content-Type should be font/woff2, got {request.content_type}"
