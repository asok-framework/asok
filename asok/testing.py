from __future__ import annotations

import io
import json
from typing import Any, Iterable
from urllib.parse import urlencode

from .core import Asok


class TestResponse:
    """Wraps a WSGI response for easy assertions during testing."""

    def __init__(self, status: str, headers: Iterable[tuple[str, str]], body: bytes):
        """Initialize the test response wrapper."""
        self.status = status
        self.status_code = int(status.split(" ", 1)[0])
        self.headers = dict(headers)
        self.body = body
        self.text = body.decode("utf-8", errors="replace")

    @property
    def json(self):
        return json.loads(self.text)

    def __contains__(self, item):
        return item in self.text


class TestClient:
    """WSGI test client that allows making requests directly to the app without a running server."""

    def __init__(self, app: Asok):
        """Initialize the test client with an Asok application instance."""
        self.app = app
        self.cookies: dict[str, str] = {}

    def _build_environ(
        self, method, path, data=None, json_body=None, headers=None, content_type=None
    ):
        query_string = ""
        if "?" in path:
            path, query_string = path.split("?", 1)

        body = b""
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            content_type = "application/json"
        elif data is not None:
            body = urlencode(data).encode("utf-8")
            content_type = content_type or "application/x-www-form-urlencoded"

        environ = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": query_string,
            "CONTENT_TYPE": content_type or "",
            "CONTENT_LENGTH": str(len(body)),
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "HTTP_HOST": "localhost",
            "wsgi.input": io.BytesIO(body),
            "wsgi.errors": io.BytesIO(),
            "wsgi.url_scheme": "http",
        }

        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            environ["HTTP_COOKIE"] = cookie_str

        for key, value in (headers or {}).items():
            wsgi_key = "HTTP_" + key.upper().replace("-", "_")
            environ[wsgi_key] = value

        return environ

    def _request(self, method, path, **kwargs):
        environ = self._build_environ(method, path, **kwargs)

        response_started = []

        def start_response(status, headers):
            response_started.append((status, headers))

        result = self.app(environ, start_response)
        body = b"".join(result)
        status, headers = response_started[0]

        # Track cookies from Set-Cookie headers
        for key, value in headers:
            if key == "Set-Cookie":
                cookie_part = value.split(";")[0]
                if "=" in cookie_part:
                    cname, cval = cookie_part.split("=", 1)
                    if "Max-Age=0" in value:
                        self.cookies.pop(cname, None)
                    else:
                        self.cookies[cname] = cval

        return TestResponse(status, headers, body)

    def get(self, path: str, **kwargs: Any) -> TestResponse:
        """Perform a GET request."""
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> TestResponse:
        """Perform a POST request."""
        return self._request("POST", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> TestResponse:
        """Perform a PUT request."""
        return self._request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> TestResponse:
        """Perform a DELETE request."""
        return self._request("DELETE", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> TestResponse:
        """Perform a PATCH request."""
        return self._request("PATCH", path, **kwargs)
