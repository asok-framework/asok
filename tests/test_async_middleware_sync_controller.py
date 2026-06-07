from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from asok.core import Asok


class DummyModuleSync:
    def get(self, request):
        return "Sync response"


async def async_middleware(request, next_handler):
    res = await next_handler(request)
    return res


def test_asgi_async_middleware_sync_controller() -> None:
    async def run() -> None:
        app = Asok()
        app.middleware_handlers.append(async_middleware)

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
