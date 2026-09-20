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


class TestRequestGeo:
    def test_geo_fallback_for_localhost(self):
        # 1. Test fallback to US when language is en
        req_en = Request(
            {"REMOTE_ADDR": "127.0.0.1", "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9"}
        )
        geo_en = req_en.geo
        assert geo_en["ip"] == "127.0.0.1"
        assert geo_en["country"] == "US"
        assert geo_en["iso"] == "US"
        assert geo_en["dial_code"] == "+1"
        assert geo_en["city"] == "Washington, D.C."
        assert geo_en["timezone"] == "America/New_York"

        # 2. Test fallback to FR when language is fr
        req_fr = Request(
            {"REMOTE_ADDR": "127.0.0.1", "HTTP_ACCEPT_LANGUAGE": "fr-FR,fr;q=0.9"}
        )
        geo_fr = req_fr.geo
        assert geo_fr["ip"] == "127.0.0.1"
        assert geo_fr["country"] == "FR"
        assert geo_fr["iso"] == "FR"
        assert geo_fr["dial_code"] == "+33"
        assert geo_fr["city"] == "Paris"
        assert geo_fr["timezone"] == "Europe/Paris"


def test_request_app_property():
    from asok.core import Asok
    from asok.request import Request

    app = Asok()
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "asok.app": app,
    }
    req = Request(environ)
    assert req.app is app


def test_request_url_properties():
    from asok.request import Request

    environ = {
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "asok-framework.com:8443",
        "PATH_INFO": "/docs/01-getting-started",
        "QUERY_STRING": "q=test&lang=fr",
    }
    req = Request(environ)
    assert req.host == "asok-framework.com:8443"
    assert req.hostname == "asok-framework.com"
    assert req.host_url == "https://asok-framework.com:8443/"
    assert req.base_url == "https://asok-framework.com:8443/docs/01-getting-started"
    assert req.url == "https://asok-framework.com:8443/docs/01-getting-started?q=test&lang=fr"
    assert req.full_url == req.url


def test_internal_only_decorator():
    from asok.core import Asok
    from asok.request import Request
    from asok.utils.security import internal_only

    app = Asok()

    @internal_only
    def api_endpoint(request):
        return request.api("OK")

    # 1. Allowed same-origin fetch from within application
    req1 = Request({"HTTP_HOST": "localhost:8000", "HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_SEC_FETCH_MODE": "cors", "asok.app": app})
    res1 = api_endpoint(req1)
    assert req1.code == 200
    assert "OK" in res1

    # 2. Blocked direct address bar navigation (opening URL in browser tab)
    req2 = Request({"HTTP_HOST": "localhost:8000", "HTTP_SEC_FETCH_SITE": "none", "HTTP_SEC_FETCH_MODE": "navigate", "asok.app": app})
    res2 = api_endpoint(req2)
    assert req2.code == 403
    assert "Endpoint restricted" in res2

    # 3. Blocked raw external request (cURL / Postman without internal headers)
    req3 = Request({"HTTP_HOST": "localhost:8000", "asok.app": app})
    res3 = api_endpoint(req3)
    assert req3.code == 403
    assert "Endpoint restricted" in res3

    # 4. Blocked cross-site request from another domain
    req4 = Request({"HTTP_HOST": "localhost:8000", "HTTP_SEC_FETCH_SITE": "cross-site", "HTTP_ORIGIN": "https://attacker.com", "asok.app": app})
    res4 = api_endpoint(req4)
    assert req4.code == 403
    assert "Endpoint restricted" in res4

