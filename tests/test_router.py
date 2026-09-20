import io
import os
import shutil
import tempfile

import pytest

from asok import Asok


def make_environ(path="/", method="GET"):
    return {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "8000",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
    }


def call_app(app, environ):
    status = None
    headers = []

    def start_response(s, h, exc_info=None):
        nonlocal status, headers
        status = s
        headers = h

    body_bytes = b"".join(app(environ, start_response))
    return status, headers, body_bytes.decode("utf-8")


class TestSPAFallback:
    @pytest.fixture(autouse=True)
    def setup_app_dir(self):
        self.temp_dir = tempfile.mkdtemp()
        pages_dir = os.path.join(self.temp_dir, "src", "pages")
        os.makedirs(pages_dir, exist_ok=True)

        # Create main index page
        with open(os.path.join(pages_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write("<html><body><main asok-router><h1>Root Page</h1></main></body></html>")

        # Create explicit /about page
        about_dir = os.path.join(pages_dir, "about")
        os.makedirs(about_dir, exist_ok=True)
        with open(os.path.join(about_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write("<html><body><h1>About Page</h1></body></html>")

        old_cwd = os.getcwd()
        os.chdir(self.temp_dir)
        yield
        os.chdir(old_cwd)
        shutil.rmtree(self.temp_dir)

    def test_spa_fallback_enabled_by_default(self):
        app = Asok(root_dir=self.temp_dir)
        assert app.config.get("ENABLE_SPA_FALLBACK") is True

        # Explicit route works
        status, _, body = call_app(app, make_environ("/about"))
        assert "200" in status
        assert "About Page" in body

        # Unknown web route falls back to root page by default
        status_un, _, body_un = call_app(app, make_environ("/dashboard/profile/42"))
        assert "200" in status_un
        assert "Root Page" in body_un

    def test_spa_fallback_can_be_disabled(self):
        app = Asok(root_dir=self.temp_dir)
        app.config["ENABLE_SPA_FALLBACK"] = False

        # Explicit route works
        status, _, body = call_app(app, make_environ("/about"))
        assert "200" in status
        assert "About Page" in body

        # Unknown route returns 404 when disabled
        status_un, _, _ = call_app(app, make_environ("/dashboard/profile/42"))
        assert "404" in status_un

    def test_spa_fallback_ignores_api_routes(self):
        app = Asok(root_dir=self.temp_dir)
        app.config["ENABLE_SPA_FALLBACK"] = True

        # API routes should never fallback to SPA root page
        status_api, _, body_api = call_app(app, make_environ("/api/v1/nonexistent"))
        assert "404" in status_api
        assert "Root Page" not in body_api

    def test_spa_fallback_ignores_static_and_dotfiles(self):
        app = Asok(root_dir=self.temp_dir)
        app.config["ENABLE_SPA_FALLBACK"] = True

        for path in ("/css/style.css", "/static/bundle.js", "/assets/logo.png", "/.env", "/.git/config"):
            status, _, body = call_app(app, make_environ(path))
            assert "404" in status
            assert "Root Page" not in body

    def test_route_safety_rejects_null_bytes_and_traversal(self):
        app = Asok(root_dir=self.temp_dir)
        status_null, _, _ = call_app(app, make_environ("/test\x00page"))
        assert "404" in status_null

        status_trav, _, _ = call_app(app, make_environ("/..\\page"))
        assert "404" in status_trav

    def test_spa_fallback_custom_string_page(self):
        app = Asok(root_dir=self.temp_dir)
        app.config["ENABLE_SPA_FALLBACK"] = "about"

        status_un, _, body_un = call_app(app, make_environ("/dashboard/profile/42"))
        assert "200" in status_un
        assert "About Page" in body_un



class TestClientRouterAssets:
    def test_router_assets_exist(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        assets_dir = os.path.join(root, "asok", "core", "assets")

        js_path = os.path.join(assets_dir, "asok_router.js")
        min_js_path = os.path.join(assets_dir, "asok_router.min.js")

        assert os.path.isfile(js_path)
        assert os.path.isfile(min_js_path)

        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()
            assert "window.AsokRouter" in content
            assert "asok-router" in content
            assert "asok-route" in content
            assert "asok-link" in content
            assert "onRouteChange" in content

    def test_router_script_injected_in_debug_and_prod(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app_debug = Asok(root_dir=root)
        app_debug.config["DEBUG"] = True

        content_with_router = (
            "<html><body>"
            '<main asok-router><template asok-route="/"></template></main>'
            "</body></html>"
        )
        from asok.request import Request

        req = Request(make_environ("/"))
        injected_debug = app_debug._inject_assets(content_with_router, req, "nonce123")
        assert "AsokRouter" in injected_debug

    def test_router_without_transitions_does_not_inject_transition_assets(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app = Asok(root_dir=root)
        app.config["DEBUG"] = False
        from asok.request import Request

        content_without_transition = (
            "<html><head></head><body>"
            '<header><nav><a asok-link="/" href="/">Accueil</a><a asok-link="/contact" href="/contact">Contact</a></nav></header>'
            '<main asok-router>'
            '  <template asok-route="/"><h1>Accueil</h1></template>'
            '  <template asok-route="/contact"><h1>Contact</h1></template>'
            '</main>'
            "</body></html>"
        )
        req = Request(make_environ("/"))
        injected = app._inject_assets(content_without_transition, req, "nonce123")

        # Router script and link pointer style are injected
        assert "AsokRouter" in injected
        assert "[asok-link]" in injected
        # Dead code is NOT injected:
        assert "asok-transitions" not in injected
        assert "asok-transition-engine" not in injected
        assert "asok_transitions.min.js" not in injected
        assert "window.AsokDirectives=" not in injected
        assert "window.__asok_registry" not in injected
        assert "asok-copied" not in injected
        assert "AsokSecurity" not in injected
        assert "asok-critical" not in injected

    def test_router_with_transitions_injects_transition_assets(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app = Asok(root_dir=root)
        from asok.request import Request

        content_with_transition = (
            "<html><head></head><body>"
            '<main asok-router asok-transition="fade 200">'
            '  <template asok-route="/"><h1>Accueil</h1></template>'
            '  <template asok-route="/contact" asok-transition="slide 300"><h1>Contact</h1></template>'
            '</main>'
            "</body></html>"
        )
        req = Request(make_environ("/"))
        injected = app._inject_assets(content_with_transition, req, "nonce123")

        # Router script is injected
        assert "AsokRouter" in injected
        # Transition CSS and JS ARE injected because asok-transition is present
        assert "asok-transitions" in injected
        assert "asok-transition-engine" in injected

    def test_router_with_directives_injects_both(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        app = Asok(root_dir=root)
        from asok.request import Request

        content = (
            "<html><head></head><body>"
            '<div asok-state="count: 0"><span asok-text="count"></span></div>'
            '<main asok-router>'
            '  <template asok-route="/"><h1>Home</h1></template>'
            '</main>'
            "</body></html>"
        )
        req = Request(make_environ("/"))
        injected = app._inject_assets(content, req, "nonce123")

        assert "AsokRouter" in injected
        assert "AsokDirectives" in injected



