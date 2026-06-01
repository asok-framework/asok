import json

import pytest

from asok import Request, current_request
from asok.background import background
from asok.component import Component
from asok.context import request_context
from asok.ws.live import on_live_message


def test_request_proxy_basic():
    """Verify that the request proxy delegates attribute access when in context,
    and raises RuntimeError when outside context.
    """
    # 1. Outside context, accessing should raise RuntimeError
    with pytest.raises(RuntimeError, match="Working outside of request context"):
        _ = current_request.path

    with pytest.raises(RuntimeError, match="Working outside of request context"):
        current_request.foo = "bar"

    with pytest.raises(RuntimeError, match="Working outside of request context"):
        del current_request.foo

    assert bool(current_request) is False
    assert repr(current_request) == "<RequestProxy [detached]>"
    assert str(current_request) == "Detached Request"

    # 2. Inside context, current_request should delegate to the active Request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/hello",
        "HTTP_HOST": "example.com",
    }
    req = Request(environ)

    with request_context(req):
        assert bool(current_request) is True
        assert current_request.path == "/hello"
        assert current_request.method == "GET"
        assert current_request.host == "example.com"
        assert repr(current_request) == repr(req)
        assert str(current_request) == str(req)

        # Setter and deleter work through the proxy
        current_request.custom_attr = 42
        assert req.custom_attr == 42
        assert current_request.custom_attr == 42

        del current_request.custom_attr
        assert not hasattr(req, "custom_attr")
        assert not hasattr(current_request, "custom_attr")

    # 3. Back outside context, should raise again
    with pytest.raises(RuntimeError, match="Working outside of request context"):
        _ = current_request.path


def test_background_context_propagation():
    """Verify that background tasks run with a copy of the caller's contextvars."""
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/bg-test",
    }
    req = Request(environ)

    def bg_task():
        # Runs in a separate ThreadPool thread — context must be propagated
        assert current_request.path == "/bg-test"
        return current_request.path

    with request_context(req):
        future = background(bg_task)
        result = future.result(timeout=2)
        assert result == "/bg-test"


class MockServer:
    def __init__(self, app):
        self.app = app
        self.secret_key = "test-secret-key-do-not-use-in-prod"


class MockConnection:
    def __init__(self):
        self.path = "/ws-portfolio"
        self.headers = {"host": "portfolio.local"}
        self.user = "mock-portfolio-user"
        self.session = None
        self.sent_messages = []
        self._live_comps = {}

    def send_json(self, data):
        self.sent_messages.append(data)


class DummyLiveComponent(Component):
    _bindable = ["title"]
    title = "My Portfolio"

    def render(self):
        # Access current_request (the global proxy) to generate dynamic content
        return (
            f"<div>"
            f"  <h1>{self.title}</h1>"
            f"  <p>Path: {current_request.path}</p>"
            f"  <p>User: {current_request.user}</p>"
            f"</div>"
        )


def test_ws_context_propagation(fresh_app):
    """Verify that WebSocket live-component re-renders run within a mock request context."""
    server = MockServer(fresh_app)
    conn = MockConnection()

    cid = "comp_test_1"
    initial_comp = DummyLiveComponent(_cid=cid)
    signed_state = initial_comp._sign_state(server.secret_key)
    conn._live_comps[cid] = (DummyLiveComponent, signed_state)

    message = {"op": "sync", "cid": cid, "prop": "title", "val": "Updated Portfolio"}
    on_live_message(server, conn, json.dumps(message))

    assert len(conn.sent_messages) == 1
    resp = conn.sent_messages[0]
    assert resp["op"] == "render"
    assert resp["cid"] == cid

    # current_request was populated from the WebSocket connection — prove it was used
    html_content = resp["html"]
    assert "Updated Portfolio" in html_content
    assert "Path: /ws-portfolio" in html_content
    assert "User: mock-portfolio-user" in html_content
