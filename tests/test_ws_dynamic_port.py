import os

from asok import Asok
from asok.request import Request


def test_ws_port_from_app_config():
    app = Asok()
    app.config["WS_PORT"] = 9090

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/page",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "wsgi.input": None,
        "asok.app": app,
    }
    req = Request(environ)
    html = "<div><span ws-click='inc'>Click</span></div>"
    injected = app._inject_assets(html, req, "test-nonce")

    assert "window.ASOK_WS_PORT = 9090;" in injected


def test_ws_port_from_environ():
    os.environ["ASOK_WS_PORT"] = "8080"
    try:
        app = Asok()
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/page",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8080",
            "wsgi.input": None,
            "asok.app": app,
        }
        req = Request(environ)
        html = "<div><span ws-click='inc'>Click</span></div>"
        injected = app._inject_assets(html, req, "test-nonce")

        assert "window.ASOK_WS_PORT = 8080;" in injected
    finally:
        os.environ.pop("ASOK_WS_PORT", None)
