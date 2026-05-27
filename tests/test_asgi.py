from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from asok.core import Asok


class DummyModuleSync:
    def get(self, request):
        return "Sync response"


class DummyModuleAsync:
    async def get(self, request):
        return "Async response"


def test_asgi_lifespan() -> None:
    async def run() -> None:
        app = Asok()
        startup_called = False
        shutdown_called = False

        async def startup_hook():
            nonlocal startup_called
            startup_called = True

        def shutdown_hook():
            nonlocal shutdown_called
            shutdown_called = True

        app._on_startup.append(startup_hook)
        app._on_shutdown.append(shutdown_hook)

        scope = {"type": "lifespan"}
        receive_events = [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]
        sent_messages = []

        async def receive():
            return receive_events.pop(0)

        async def send(message):
            sent_messages.append(message)

        await app(scope, receive, send)

        assert startup_called is True
        assert shutdown_called is True
        assert sent_messages == [
            {"type": "lifespan.startup.complete"},
            {"type": "lifespan.shutdown.complete"},
        ]

    asyncio.run(run())


def test_asgi_http_routing_sync_controller() -> None:
    async def run() -> None:
        app = Asok()

        app._resolve_route = MagicMock(return_value=("mock_sync_page.py", {}))
        app._load_module = MagicMock(return_value=DummyModuleSync())

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/sync",
            "headers": [(b"host", b"localhost:8000")],
            "http_version": "1.1",
            "scheme": "http",
        }

        receive_events = [{"type": "http.request", "body": b"", "more_body": False}]
        sent_messages = []

        async def receive():
            return receive_events.pop(0)

        async def send(message):
            sent_messages.append(message)

        await app(scope, receive, send)

        assert any(
            m["type"] == "http.response.start" and m["status"] == 200
            for m in sent_messages
        )
        response_body = b"".join(
            m["body"] for m in sent_messages if m["type"] == "http.response.body"
        )
        assert b"Sync response" in response_body

    asyncio.run(run())


def test_asgi_http_routing_async_controller() -> None:
    async def run() -> None:
        app = Asok()

        app._resolve_route = MagicMock(return_value=("mock_async_page.py", {}))
        app._load_module = MagicMock(return_value=DummyModuleAsync())

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/async",
            "headers": [(b"host", b"localhost:8000")],
            "http_version": "1.1",
            "scheme": "http",
        }

        receive_events = [{"type": "http.request", "body": b"", "more_body": False}]
        sent_messages = []

        async def receive():
            return receive_events.pop(0)

        async def send(message):
            sent_messages.append(message)

        await app(scope, receive, send)

        assert any(
            m["type"] == "http.response.start" and m["status"] == 200
            for m in sent_messages
        )
        response_body = b"".join(
            m["body"] for m in sent_messages if m["type"] == "http.response.body"
        )
        assert b"Async response" in response_body

    asyncio.run(run())


def test_wsgi_handles_async_controller() -> None:
    app = Asok()

    app._resolve_route = MagicMock(return_value=("mock_async_page.py", {}))
    app._load_module = MagicMock(return_value=DummyModuleAsync())

    from asok.testing import TestClient

    client = TestClient(app)
    resp = client.get("/async")
    assert resp.status_code == 200
    assert "Async response" in resp.text
