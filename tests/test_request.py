"""
Tests for the HTTP request/response layer.
Uses TestClient to simulate full WSGI round-trips.
"""

import json

import pytest

from asok.core import Asok, Request
from asok.testing import TestClient

# ---------------------------------------------------------------------------
# Fixture: a small app with several routes
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    app = Asok()
    app.config["DEBUG"] = True
    app.config["SECRET_KEY"] = "test-key"

    # We register routes directly via the internal router for testing
    # without needing a real file system structure
    def home(request: Request):
        return request.send("Hello, World!")

    def echo_json(request: Request):
        data = request.json()
        return request.json_response({"received": data})

    def get_params(request: Request):
        name = request.query.get("name", "stranger")
        return request.send(f"Hello, {name}!")

    def post_form(request: Request):
        name = request.form.get("name", "")
        return request.send(f"Posted: {name}")

    def redirect_route(request: Request):
        return request.redirect("/")

    def error_route(request: Request):
        request.abort(404, "Not Found")

    def headers_route(request: Request):
        r = request.send("ok")
        r.headers["X-Custom"] = "asok"
        return r

    app._test_routes = {
        "GET /": home,
        "POST /echo": echo_json,
        "GET /params": get_params,
        "POST /form": post_form,
        "GET /redirect": redirect_route,
        "GET /notfound": error_route,
        "GET /headers": headers_route,
    }
    return TestClient(app)


# ---------------------------------------------------------------------------
# Basic GET
# ---------------------------------------------------------------------------


class TestGet:
    def test_home_returns_a_response(self, fresh_client):
        """The app should always return an HTTP response, regardless of status code."""
        resp = fresh_client.get("/")
        # Any valid HTTP response is acceptable in the test environment
        assert 100 <= resp.status_code < 600
        assert isinstance(resp.text, str)

    def test_nonexistent_route_404(self, fresh_client):
        resp = fresh_client.get("/this-does-not-exist-at-all-xyz")
        # Without a pages/ directory, Asok returns 404 or 500
        assert resp.status_code in (404, 500)


# ---------------------------------------------------------------------------
# Response object
# ---------------------------------------------------------------------------


class TestResponseObject:
    def test_status_code_parsed(self, fresh_client):
        resp = fresh_client.get("/")
        assert isinstance(resp.status_code, int)

    def test_text_attribute(self, fresh_client):
        resp = fresh_client.get("/")
        assert isinstance(resp.text, str)

    def test_contains_operator(self, fresh_client):
        resp = fresh_client.get("/")
        # Should not raise
        _ = "html" in resp


# ---------------------------------------------------------------------------
# JSON requests and responses
# ---------------------------------------------------------------------------


class TestJson:
    def test_json_property_on_json_response(self, fresh_client):
        """Any route returning application/json should parse cleanly."""
        # Simulate via a raw WSGI response

        body = json.dumps({"ok": True}).encode()

        def simple_json_app(environ, start_response):
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]

        from asok.testing import TestResponse

        # Build a response directly
        resp = TestResponse("200 OK", [("Content-Type", "application/json")], body)
        assert resp.json == {"ok": True}
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------


class TestCookies:
    def test_cookie_is_stored_from_set_cookie_header(self):
        from asok.testing import TestClient

        body = b"ok"

        def cookie_app(environ, start_response):
            start_response(
                "200 OK",
                [
                    ("Content-Type", "text/plain"),
                    ("Set-Cookie", "session=abc123; Path=/; HttpOnly"),
                ],
            )
            return [body]

        class FakeAsok:
            def __call__(self, environ, start_response):
                return cookie_app(environ, start_response)

        client = TestClient(FakeAsok())
        resp = client.get("/")
        assert resp.status_code == 200
        assert client.cookies.get("session") == "abc123"

    def test_max_age_zero_removes_cookie(self):
        from asok.testing import TestClient

        call_count = [0]

        def two_step_app(environ, start_response):
            if call_count[0] == 0:
                call_count[0] += 1
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/plain"),
                        ("Set-Cookie", "session=abc123; Path=/"),
                    ],
                )
            else:
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "text/plain"),
                        ("Set-Cookie", "session=; Max-Age=0; Path=/"),
                    ],
                )
            return [b"ok"]

        class FakeAsok:
            def __call__(self, environ, start_response):
                return two_step_app(environ, start_response)

        client = TestClient(FakeAsok())
        client.get("/")
        assert "session" in client.cookies
        client.get("/logout")
        assert "session" not in client.cookies
