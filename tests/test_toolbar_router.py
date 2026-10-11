import os
from unittest.mock import MagicMock

from asok.core import Asok
from asok.testing import TestClient
from asok.toolbar import DeveloperToolbar


def test_toolbar_router_unit_helpers():
    request = MagicMock()
    request.headers = {}
    request.args = {"q": "search"}
    request.params = {"user_id": "99"}
    request.page_id = "users-detail"
    request._current_page_file = "/var/app/pages/users/[user_id].py"
    request.scoped_assets = {"css": "pages/users/[user_id].css"}
    request.session = {}
    request.method = "GET"
    request.path = "/users/99"
    request.ip = "127.0.0.1"
    request.cookies_dict = {}
    request._asok_templates = []
    request._asok_blocks = []
    request._asok_sql_log = []

    app = MagicMock()
    app.root_dir = "/var/app"
    app.version = "1.0.0"
    app.middlewares = []
    app.config = {"DATABASE": "sqlite:///test.db", "DEBUG": True}

    toolbar = DeveloperToolbar(request, app)

    # 1. Standard render type
    assert toolbar._get_request_render_type() == "Full Page Load"

    # 2. X-Block render type
    request.headers = {"x-block": "sidebar"}
    assert toolbar._get_request_render_type() == "SPA Block (sidebar)"

    # 3. Prefetch render type
    request.headers = {"x-prefetch": "1"}
    assert toolbar._get_request_render_type() == "Prefetch Request"

    # 4. Route info
    info = toolbar._extract_server_route_info()
    assert info["Endpoint / File"] == "pages/users/[user_id].py"
    assert info["Page ID"] == "users-detail"
    assert info["Route Params"] == {"user_id": "99"}
    assert info["Query Params"] == {"q": "search"}
    assert info["Database"] == "sqlite:///test.db"

    # 5. Render output contains Router tab and placeholders
    html = toolbar.render()
    assert 'data-tab="router"' in html
    assert "Router (SPA)" in html
    assert 'id="tab-router"' in html
    assert 'id="asok-spa-log-rows"' in html
    assert 'id="asok-client-router-info"' in html
    assert 'id="asok-spa-routes-list"' in html
    assert "users-detail" in html


def test_toolbar_router_integration(tmp_dir):
    pages_dir = os.path.join(tmp_dir, "src", "pages", "items", "[id]")
    os.makedirs(pages_dir, exist_ok=True)

    page_py_path = os.path.join(pages_dir, "page.py")
    with open(page_py_path, "w", encoding="utf-8") as f:
        f.write(
            "def get(request):\n"
            "    item_id = request.params.get('id')\n"
            "    return f'<html><body><h1>Item {item_id}</h1></body></html>'\n"
        )

    app = Asok(root_dir=tmp_dir)
    app.config["DEBUG"] = True
    app.config["TOOLBAR"] = True
    app.config["SECRET_KEY"] = "super-secret-key-12345678"
    app.config["DATABASE"] = ":memory:"

    client = TestClient(app)
    response = client.get("/items/123?filter=all")

    assert response.status_code == 200
    assert "Item 123" in response.text
    assert 'id="asok-debug-trigger"' in response.text
    assert 'data-tab="router"' in response.text
    assert "items-id" in response.text or "items" in response.text
    assert "123" in response.text


def test_toolbar_router_spa_block_and_prefetch_modes():
    request = MagicMock()
    request.headers = {"x-block": "user-profile"}
    request.args = {}
    request.params = {}
    request.page_id = "profile"
    request._current_page_file = None
    request.scoped_assets = {}
    request.session = {}
    request.method = "GET"
    request.path = "/profile"
    request.ip = "127.0.0.1"
    request.cookies_dict = {}
    request._asok_templates = []
    request._asok_blocks = ["user-profile"]
    request._asok_sql_log = []

    app = MagicMock()
    app.root_dir = "/app"
    app.version = "2.0.0"
    app.middlewares = []
    app.config = {"DATABASE": "test.db", "DEBUG": True}

    toolbar = DeveloperToolbar(request, app)
    html = toolbar.render()
    assert "SPA Block (user-profile)" in html

    request.headers = {"x-prefetch": "1"}
    toolbar_prefetch = DeveloperToolbar(request, app)
    html_prefetch = toolbar_prefetch.render()
    assert "Prefetch Request" in html_prefetch


def test_toolbar_router_with_middlewares_and_scoped_assets():
    def custom_middleware(req, nxt):
        return nxt(req)

    request = MagicMock()
    request.headers = {}
    request.args = {}
    request.params = {}
    request.page_id = "dashboard"
    request._current_page_file = "/workspace/pages/dashboard.py"
    request.scoped_assets = {"css": "dashboard.css", "js": "dashboard.js"}
    request.session = {}
    request.method = "GET"
    request.path = "/dashboard"
    request.ip = "127.0.0.1"
    request.cookies_dict = {}
    request._asok_templates = []
    request._asok_blocks = []
    request._asok_sql_log = []

    app = MagicMock()
    app.root_dir = "/workspace"
    app.version = "1.0.0"
    app.middlewares = [custom_middleware]
    app.config = {"DATABASE": "postgres://localhost/mydb", "DEBUG": True}

    toolbar = DeveloperToolbar(request, app)
    html = toolbar.render()

    assert "custom_middleware" in html
    assert "dashboard.css" in html
    assert "postgres://localhost/mydb" in html
    assert "pages/dashboard.py" in html
