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
            f.write(
                "<html><body><main asok-router><h1>Root Page</h1></main></body></html>"
            )

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

        for path in (
            "/css/style.css",
            "/static/bundle.js",
            "/assets/logo.png",
            "/.env",
            "/.git/config",
        ):
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
            "<main asok-router>"
            '  <template asok-route="/"><h1>Accueil</h1></template>'
            '  <template asok-route="/contact"><h1>Contact</h1></template>'
            "</main>"
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
            "</main>"
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
            "<main asok-router>"
            '  <template asok-route="/"><h1>Home</h1></template>'
            "</main>"
            "</body></html>"
        )
        req = Request(make_environ("/"))
        injected = app._inject_assets(content, req, "nonce123")

        assert "AsokRouter" in injected
        assert "AsokDirectives" in injected


class TestNonSPARoutingAnd404:
    @pytest.fixture(autouse=True)
    def setup_app_dir(self):
        self.temp_dir = tempfile.mkdtemp()
        self.pages_dir = os.path.join(self.temp_dir, "src", "pages")
        os.makedirs(self.pages_dir, exist_ok=True)

        old_cwd = os.getcwd()
        os.chdir(self.temp_dir)
        yield
        os.chdir(old_cwd)
        shutil.rmtree(self.temp_dir)

    def test_non_spa_app_returns_404_for_unknown_routes(self):
        # Root page without asok-router
        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write("<html><body><h1>Standard Home Page</h1></body></html>")

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/kslsls"))
        assert "404" in status
        assert "<h1>404</h1>" in body
        assert "Standard Home Page" not in body

    def test_non_spa_app_renders_custom_404_html_page(self):
        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write("<html><body><h1>Standard Home Page</h1></body></html>")

        error_404_dir = os.path.join(self.pages_dir, "404")
        os.makedirs(error_404_dir, exist_ok=True)
        with open(os.path.join(error_404_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write("<html><body><h1>Désolé, page non trouvée (404)</h1></body></html>")

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/kslsls"))
        assert "404" in status
        assert "Désolé, page non trouvée (404)" in body
        assert "Standard Home Page" not in body

    def test_non_spa_app_renders_custom_404_py_page(self):
        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write("<html><body><h1>Standard Home Page</h1></body></html>")

        error_404_dir = os.path.join(self.pages_dir, "404")
        os.makedirs(error_404_dir, exist_ok=True)
        with open(os.path.join(error_404_dir, "page.py"), "w", encoding="utf-8") as f:
            f.write("def render(req):\n    return '<div>Custom Python 404</div>'\n")

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/kslsls"))
        assert "404" in status
        assert "Custom Python 404" in body

    def test_spa_fallback_when_extended_layout_uses_router(self):
        partials_dir = os.path.join(self.temp_dir, "src", "partials")
        os.makedirs(partials_dir, exist_ok=True)
        with open(
            os.path.join(partials_dir, "layout.html"), "w", encoding="utf-8"
        ) as f:
            f.write(
                "<html><body><main asok-router>{% block content %}{% endblock %}</main></body></html>"
            )

        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write(
                '{% extends "layout.html" %}\n{% block content %}<h1>Root Extended</h1>{% endblock %}'
            )

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/kslsls"))
        assert "200" in status
        assert "Root Extended" in body

    def test_404_when_extended_layout_does_not_use_router(self):
        partials_dir = os.path.join(self.temp_dir, "src", "partials")
        os.makedirs(partials_dir, exist_ok=True)
        with open(os.path.join(partials_dir, "base.html"), "w", encoding="utf-8") as f:
            f.write(
                "<html><body><div>{% block content %}{% endblock %}</div></body></html>"
            )

        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write(
                '{% extends "base.html" %}\n{% block content %}<h1>Non-SPA Extended</h1>{% endblock %}'
            )

        error_404_dir = os.path.join(self.pages_dir, "404")
        os.makedirs(error_404_dir, exist_ok=True)
        with open(os.path.join(error_404_dir, "page.html"), "w", encoding="utf-8") as f:
            f.write("<h1>Base 404 Error</h1>")

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/kslsls"))
        assert "404" in status
        assert "Base 404 Error" in body
        assert "Non-SPA Extended" not in body

    def test_spa_fallback_when_root_is_page_py_rendering_router_template(self):
        partials_html = os.path.join(self.temp_dir, "src", "partials", "html")
        os.makedirs(partials_html, exist_ok=True)
        with open(os.path.join(partials_html, "base.html"), "w", encoding="utf-8") as f:
            f.write(
                "<!DOCTYPE html><html><body><main>{% block main %}{% endblock %}</main></body></html>"
            )

        tpl = (
            '{% extends "html/base.html" %}\n'
            "{% block main %}\n"
            "    <div asok-router>\n"
            '        <template asok-route="/"><h1>Accueil</h1></template>\n'
            '        <template asok-route="/contact"><h1>Contact</h1></template>\n'
            '        <template asok-route="*"><h1>La page d\'erreur</h1></template>\n'
            "    </div>\n"
            "{% endblock %}\n"
        )
        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write(tpl)

        with open(os.path.join(self.pages_dir, "page.py"), "w", encoding="utf-8") as f:
            f.write(
                "from asok import Request\n\ndef render(request: Request):\n    return request.html('page.html')\n"
            )

        app = Asok(root_dir=self.temp_dir)
        # Declared route in SPA returns 200 OK
        status_contact, _, body_contact = call_app(app, make_environ("/contact"))
        assert "200" in status_contact
        assert "Contact" in body_contact

        # Unknown route matching wildcard * returns 404 Not Found while serving SPA error view
        status_err, _, body_err = call_app(app, make_environ("/lslskd"))
        assert "404" in status_err
        assert "asok-router" in body_err
        assert "La page d'erreur" in body_err

    def test_404_when_root_is_page_py_rendering_non_router_template(self):
        with open(
            os.path.join(self.pages_dir, "page.html"), "w", encoding="utf-8"
        ) as f:
            f.write("<html><body><h1>Normal Page</h1></body></html>")

        with open(os.path.join(self.pages_dir, "page.py"), "w", encoding="utf-8") as f:
            f.write(
                "from asok import Request\n\ndef render(request: Request):\n    return request.html('page.html')\n"
            )

        app = Asok(root_dir=self.temp_dir)
        status, _, body = call_app(app, make_environ("/lslskd"))
        assert "404" in status
        assert "<h1>404</h1>" in body
