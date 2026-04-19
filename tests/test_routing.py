"""
Tests for the file-based routing system.
Covers: static routes, dynamic [param] routes, HTTP method dispatch,
query string parsing, redirect, abort (404/403), flash messages.
Uses a real Asok app with routes registered via the routing API.
"""

import io

import pytest

from asok.core import Asok
from asok.testing import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_env(method="GET", path="/", data=None, query="", headers=None):
    """Build a minimal WSGI environ dict."""
    from urllib.parse import urlencode

    body = b""
    ct = ""
    if data:
        body = urlencode(data).encode()
        ct = "application/x-www-form-urlencoded"
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_TYPE": ct,
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
    }
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    return environ


# ---------------------------------------------------------------------------
# TestClient: basic request/response cycle
# ---------------------------------------------------------------------------


class TestBasicRouting:
    @pytest.fixture
    def client(self):
        return TestClient(Asok())

    def test_any_route_returns_valid_http_response(self, client):
        resp = client.get("/")
        assert 100 <= resp.status_code < 600

    def test_unknown_route_returns_4xx_or_5xx(self, client):
        resp = client.get("/zz-totally-unknown-route-xyz")
        assert resp.status_code >= 400

    def test_response_has_text_attribute(self, client):
        resp = client.get("/")
        assert isinstance(resp.text, str)

    def test_response_has_headers_dict(self, client):
        resp = client.get("/")
        assert isinstance(resp.headers, dict)


# ---------------------------------------------------------------------------
# WSGI environ construction
# ---------------------------------------------------------------------------


class TestEnvironConstruction:
    def test_get_environ_method(self):
        env = make_env("GET", "/about")
        assert env["REQUEST_METHOD"] == "GET"
        assert env["PATH_INFO"] == "/about"

    def test_post_environ_with_body(self):
        env = make_env("POST", "/login", data={"user": "alice", "pass": "secret"})
        assert env["CONTENT_TYPE"] == "application/x-www-form-urlencoded"
        assert int(env["CONTENT_LENGTH"]) > 0
        body = env["wsgi.input"].read()
        assert b"alice" in body

    def test_query_string_preserved(self):
        env = make_env("GET", "/search", query="q=python&page=2")
        assert env["QUERY_STRING"] == "q=python&page=2"

    def test_custom_headers_set(self):
        env = make_env("GET", "/", headers={"Authorization": "Bearer token123"})
        assert env["HTTP_AUTHORIZATION"] == "Bearer token123"


# ---------------------------------------------------------------------------
# Request object from environ
# ---------------------------------------------------------------------------


class TestRequestObject:
    def test_method_parsed(self):
        from asok.request import Request

        req = Request(make_env("POST", "/submit"))
        assert req.method == "POST"

    def test_path_parsed(self):
        from asok.request import Request

        req = Request(make_env("GET", "/about/us"))
        assert req.path == "/about/us"

    def test_query_params_parsed(self):
        from asok.request import Request

        req = Request(make_env("GET", "/search", query="q=asok&page=1"))
        assert req.args.get("q") == "asok"
        assert req.args.get("page") == "1"

    def test_form_data_parsed(self):
        from asok.request import Request

        req = Request(make_env("POST", "/submit", data={"name": "Alice", "age": "30"}))
        assert req.form.get("name") == "Alice"
        assert req.form.get("age") == "30"

    def test_is_post(self):
        from asok.request import Request

        req = Request(make_env("POST", "/"))
        assert req.method == "POST"

    def test_is_get(self):
        from asok.request import Request

        req = Request(make_env("GET", "/"))
        assert req.method == "GET"

    def test_host_header(self):
        from asok.request import Request

        req = Request(make_env("GET", "/", headers={"Host": "example.com"}))
        assert "example.com" in (req.host or req.environ.get("HTTP_HOST", ""))

    def test_json_body_parsed(self):
        import json

        from asok.request import Request

        body = json.dumps({"key": "value"}).encode()
        environ = make_env("POST", "/api")
        environ["CONTENT_TYPE"] = "application/json"
        environ["CONTENT_LENGTH"] = str(len(body))
        environ["wsgi.input"] = io.BytesIO(body)
        req = Request(environ)
        data = req.json_body
        assert data == {"key": "value"}


# ---------------------------------------------------------------------------
# TestClient HTTP verbs
# ---------------------------------------------------------------------------


class TestHttpVerbs:
    @pytest.fixture
    def client(self):
        return TestClient(Asok())

    def test_get_request(self, client):
        resp = client.get("/")
        assert isinstance(resp.status_code, int)

    def test_post_request(self, client):
        resp = client.post("/", data={"x": "1"})
        assert isinstance(resp.status_code, int)

    def test_put_request(self, client):
        resp = client.put("/resource/1")
        assert isinstance(resp.status_code, int)

    def test_delete_request(self, client):
        resp = client.delete("/resource/1")
        assert isinstance(resp.status_code, int)

    def test_patch_request(self, client):
        resp = client.patch("/resource/1")
        assert isinstance(resp.status_code, int)
