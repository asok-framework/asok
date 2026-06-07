from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from typing import Any

from asok.cli.main import load_extension_commands
from asok.core.asok import Asok
from asok.core.extension import AsokExtension
from asok.core.signals import (
    app_shutdown,
    app_startup,
    request_finished,
    request_started,
)
from asok.request import Request
from asok.templates import render_template_string


class DummyExtension(AsokExtension):
    """A dummy extension for testing purposes."""

    def __init__(self, pages_dir: str, templates_dir: str, static_dir: str) -> None:
        super().__init__()
        self._pages_dir = pages_dir
        self._templates_dir = templates_dir
        self._static_dir = static_dir
        self.startup_called = False
        self.shutdown_called = False
        self.req_started_called = False
        self.req_finished_called = False

    def init_app(self, app: Asok) -> None:
        super().init_app(app)
        app_startup.connect(self.on_startup)
        app_shutdown.connect(self.on_shutdown)
        request_started.connect(self.on_request_started)
        request_finished.connect(self.on_request_finished)

    def on_startup(self, sender: Any) -> None:
        self.startup_called = True

    def on_shutdown(self, sender: Any) -> None:
        self.shutdown_called = True

    def on_request_started(self, sender: Any, request: Request) -> None:
        self.req_started_called = True

    def on_request_finished(self, sender: Any, request: Request) -> None:
        self.req_finished_called = True

    def get_pages_path(self) -> str:
        return self._pages_dir

    def get_templates_path(self) -> str:
        return self._templates_dir

    def get_static_path(self) -> str:
        return self._static_dir


def test_extension_system() -> None:
    # 1. Setup a temporary directory hierarchy for the app and the extension
    temp_dir = tempfile.mkdtemp()
    try:
        # Create app structure
        app_root = os.path.join(temp_dir, "my_app")
        os.makedirs(os.path.join(app_root, "src", "pages"))
        os.makedirs(os.path.join(app_root, "src", "partials"))
        os.makedirs(os.path.join(app_root, "src", "components"))
        os.makedirs(os.path.join(app_root, "src", "partials", "css"))

        # Create extension structure
        ext_root = os.path.join(temp_dir, "my_extension")
        ext_pages = os.path.join(ext_root, "pages")
        ext_tpls = os.path.join(ext_root, "templates")
        ext_static_base = os.path.join(ext_root, "static")
        ext_static_css = os.path.join(ext_static_base, "css")
        os.makedirs(ext_pages)
        os.makedirs(ext_tpls)
        os.makedirs(ext_static_css)

        # Write dummy files to the extension
        # A. Page file
        ext_page_content = """
def get(req):
    return "Hello from Extension Page"
"""
        with open(os.path.join(ext_pages, "ext_info.py"), "w", encoding="utf-8") as f:
            f.write(ext_page_content)

        # B. Template file
        ext_tpl_content = "<div>Extension Partial Content</div>"
        with open(os.path.join(ext_tpls, "ext_partial.html"), "w", encoding="utf-8") as f:
            f.write(ext_tpl_content)

        # C. Static file
        ext_style_content = "body { background: purple; }"
        with open(os.path.join(ext_static_css, "ext_style.css"), "w", encoding="utf-8") as f:
            f.write(ext_style_content)

        # 2. Instantiate Asok app
        # Force session path to be relative and safe under temp_dir
        os.environ["SECRET_KEY"] = "super-secret-key-that-is-at-least-32-chars-long"
        app = Asok(root_dir=app_root)
        app.config["SESSION_PATH"] = ".asok/sessions"

        # 3. Instantiate and register extension
        ext = DummyExtension(ext_pages, ext_tpls, ext_static_base)
        app.register_extension(ext)

        assert "DummyExtension" in app.extensions

        # 4. Verify Pages routing from Extension
        page_file, route_params = app._resolve_route(["ext_info"])
        assert page_file is not None
        assert os.path.basename(page_file) == "ext_info.py"

        # 5. Verify Template processing (using include)
        main_tpl = "{% include 'ext_partial.html' %}"
        rendered = render_template_string(main_tpl, {}, root_dir=app._template_search_paths)
        assert "Extension Partial Content" in rendered

        # 6. Verify Static files serving from Extension
        # Simulate request object for static files handling
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/css/ext_style.css",
            "wsgi.url_scheme": "http",
        }
        req = Request(environ)

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            assert "200" in status

        res = app._handle_static_request(req, environ, start_response)
        assert res is not None
        assert ext_style_content.encode("utf-8") in res[0]

        # 7. Verify signals dispatching
        # Test startup / shutdown
        assert not ext.startup_called
        app.startup()
        assert ext.startup_called

        assert not ext.shutdown_called
        app.shutdown()
        assert ext.shutdown_called

        # Test request lifecycle signals
        assert not ext.req_started_called
        assert not ext.req_finished_called

        # Invoke wsgi call on basic path to trigger signals
        app_wsgi_environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/ext_info",
            "wsgi.url_scheme": "http",
        }

        def mock_start_response(status: str, headers: list[tuple[str, str]]) -> None:
            pass

        # Call wsgi entry point which sets up request and runs finally block
        app._wsgi_call(app_wsgi_environ, mock_start_response)

        assert ext.req_started_called
        assert ext.req_finished_called

    finally:
        shutil.rmtree(temp_dir)
        # Disconnect signal listeners to prevent leaking into other tests
        app_startup.disconnect(ext.on_startup)
        app_shutdown.disconnect(ext.on_shutdown)
        request_started.disconnect(ext.on_request_started)
        request_finished.disconnect(ext.on_request_finished)


def test_extension_cli_loading() -> None:
    """Verify load_extension_commands doesn't crash even if entrypoints are empty."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers()
    # Call to make sure no exception is raised
    load_extension_commands(subparsers)
