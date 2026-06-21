"""Tests for second security audit fixes.

Covers:
1. Open redirect vulnerability validation via HTTP_REFERER.
2. WebSocket room authorization default policy.
"""

from asok.admin import Admin
from asok.request import Request
from asok.ws import Connection, WebSocketServer


class DummyApp:
    def __init__(self, root_dir=None):
        self.root_dir = root_dir or "."
        self.config = {
            "SECRET_KEY": "test_secret",
            "DATABASE": ":memory:",
            "AUTH_MODEL": "User",
        }
        self.models = []


def test_resolve_redirect_referer_safe_url():
    """Verify that _resolve_redirect_referer validates the domain using is_safe_url."""
    app = DummyApp()
    admin = Admin(app)

    # 1. Valid referer on the same host
    environ = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_REFERER": "http://localhost:8000/admin/dashboard",
    }
    req = Request(environ)
    assert admin._resolve_redirect_referer(req) == "http://localhost:8000/admin/dashboard"

    # 2. Referer on an external domain (unsafe) -> redirects to prefix
    environ = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_REFERER": "https://evil.com/fake-login",
    }
    req = Request(environ)
    assert admin._resolve_redirect_referer(req) == admin.prefix

    # 3. Relative referer (safe)
    environ = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_REFERER": "/admin/dashboard",
    }
    req = Request(environ)
    assert admin._resolve_redirect_referer(req) == "/admin/dashboard"

    # 4. Referer containing "/lang" -> redirects to prefix to prevent redirect loops
    environ = {
        "HTTP_HOST": "localhost:8000",
        "HTTP_REFERER": "http://localhost:8000/admin/lang?lang=en",
    }
    req = Request(environ)
    assert admin._resolve_redirect_referer(req) == admin.prefix


def test_websocket_room_authorization_default():
    """Verify default room authorization denies non-model: rooms when no authorizer is set."""
    app = DummyApp()
    server = WebSocketServer(app=app)

    class MockSocket:
        pass

    conn = Connection(MockSocket(), ("127.0.0.1", 1234), "/", {}, server=server)

    # Without room_authorizer:
    # 1. model: rooms should be allowed (since they have their own validation in live.py)
    assert server.check_room_authorization(conn, "model:User:1") is True
    assert server.check_room_authorization(conn, "model:User") is True

    # 2. Custom rooms should be denied by default
    assert server.check_room_authorization(conn, "chat:room1") is False
    assert server.check_room_authorization(conn, "order:123") is False


def test_websocket_room_authorization_custom():
    """Verify custom room authorizer override behaves as expected."""
    app = DummyApp()
    server = WebSocketServer(app=app)

    @server.room_authorizer
    def my_authorizer(conn, room):
        return room == "chat:room1"

    class MockSocket:
        pass

    conn = Connection(MockSocket(), ("127.0.0.1", 1234), "/", {}, server=server)

    # Custom authorizer should be respected
    assert server.check_room_authorization(conn, "chat:room1") is True
    assert server.check_room_authorization(conn, "model:User:1") is False
    assert server.check_room_authorization(conn, "order:123") is False
