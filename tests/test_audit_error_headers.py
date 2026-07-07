import asyncio
import os
from unittest.mock import MagicMock

from asok.core.asok import Asok
from asok.core.wsgi import _FinalResponseException
from asok.request import Request


def test_wsgi_unhandled_exception_headers(tmp_path):
    root_dir = str(tmp_path)
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "super-secret-key-that-is-at-least-32-chars-long"

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/error",
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
    }
    request = Request(environ)

    captured_status = ""
    captured_headers = []

    def start_response(status, headers):
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = headers

    # Simulate unhandled exception in controller
    e = ValueError("Simulated unhandled exception")
    app._send_wsgi_error(request, e, start_response, environ)

    assert "500" in captured_status
    headers_dict = {k.lower(): v for k, v in captured_headers}

    # Assert security headers are present
    assert "x-content-type-options" in headers_dict
    assert "x-frame-options" in headers_dict
    assert "content-security-policy" in headers_dict


def test_ssg_isr_cache_serving_headers(tmp_path):
    root_dir = str(tmp_path)
    app = Asok(root_dir=root_dir)
    app.config["SECRET_KEY"] = "super-secret-key-that-is-at-least-32-chars-long"
    app.config["DEBUG"] = False

    cache_dir = app._get_ssg_cache_dir()
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = app._get_ssg_cache_file("/about")
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write("<h1>About Cached</h1>")

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/about",
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
    }
    request = Request(environ)

    captured_status = ""
    captured_headers = []

    def start_response(status, headers):
        nonlocal captured_status, captured_headers
        captured_status = status
        captured_headers = headers

    # Execute SSG cached page serve
    response = app._send_cached_file(request, cache_file, None, start_response)

    assert response is not None
    assert captured_status == "200 OK"
    headers_dict = {k.lower(): v for k, v in captured_headers}
    assert headers_dict.get("x-asok-ssg-cache") == "HIT"
    assert "x-content-type-options" in headers_dict
    assert "content-security-policy" in headers_dict


def test_asgi_unhandled_exception_headers_and_final_response():
    async def run() -> None:
        app = Asok()
        app.config["SECRET_KEY"] = "super-secret-key-that-is-at-least-32-chars-long"

        # 1. Test _send_final_response_exception in ASGI
        fre = _FinalResponseException(b"Not Found Page", 404, content_type="text/html")
        sent_messages = []

        async def send(message):
            sent_messages.append(message)

        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/notfound",
            "wsgi.url_scheme": "http",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
        }
        request = Request(environ)

        await app._send_final_response_exception(fre, send, request, environ)

        start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
        assert start_msg["status"] == 404
        headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
        assert b"content-security-policy" in headers_dict
        assert b"x-frame-options" in headers_dict
        assert b"content-length" in headers_dict

        # 2. Test ASGI global try-except handling unhandled exceptions
        app._resolve_route = MagicMock(return_value=("mock_crash.py", {}))

        class CrashingModule:
            def get(self, request):
                raise ValueError("Crashing controller")

        app._load_module = MagicMock(return_value=CrashingModule())

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/crash",
            "headers": [(b"host", b"localhost:8000")],
            "http_version": "1.1",
            "scheme": "http",
        }
        receive_events = [{"type": "http.request", "body": b"", "more_body": False}]
        sent_messages.clear()

        async def receive():
            return receive_events.pop(0)

        await app(scope, receive, send)

        start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
        assert start_msg["status"] == 500
        headers_dict = {k.lower(): v for k, v in start_msg["headers"]}
        assert b"content-security-policy" in headers_dict
        assert b"x-frame-options" in headers_dict

    asyncio.run(run())
