from __future__ import annotations

from unittest.mock import MagicMock

from asok.core import Asok
from asok.testing import TestClient


class DummyModuleSync:
    def get(self, request):
        return "Sync response"

async def async_middleware(request, next_handler):
    res = await next_handler(request)
    return res

def test_wsgi_async_middleware_sync_controller() -> None:
    app = Asok()
    app.middleware_handlers.append(async_middleware)

    app._resolve_route = MagicMock(return_value=("mock_sync_page.py", {}))
    app._load_module = MagicMock(return_value=DummyModuleSync())

    client = TestClient(app)
    resp = client.get("/sync")
    assert resp.status_code == 200
    assert "Sync response" in resp.text
