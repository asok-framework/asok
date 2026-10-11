import os

from asok.core import Asok
from asok.testing import TestClient


def test_router_with_live_component_and_directives_integration(tmp_dir):
    # Base layout in src/partials/html/base.html
    templates_dir = os.path.join(tmp_dir, "src", "partials", "html")
    os.makedirs(templates_dir, exist_ok=True)
    with open(os.path.join(templates_dir, "base.html"), "w", encoding="utf-8") as f:
        f.write(
            "<!DOCTYPE html>\n"
            "<html><head><title>Test App</title></head><body>\n"
            '<header><nav><a href="/" asok-link>Home</a><a href="/contact" asok-link>Contact</a></nav></header>\n'
            "<main asok-router>{% block main %}{% endblock %}</main>\n"
            "</body></html>"
        )

    # Page template with router, directives and a Live Component
    pages_dir = os.path.join(tmp_dir, "src", "pages")
    os.makedirs(pages_dir, exist_ok=True)
    with open(os.path.join(pages_dir, "page.html"), "w", encoding="utf-8") as f:
        f.write(
            '{% extends "html/base.html" %}\n'
            "{% block main %}\n"
            '<template asok-route="/" asok-meta:title="Accueil">\n'
            "  <section asok-state=\"{ message: 'Hello World' }\">\n"
            '    <h1 asok-text="message"></h1>\n'
            "  </section>\n"
            "</template>\n"
            '<template asok-route="/contact" asok-meta:title="Contact">\n'
            "  <section>\n"
            "    <h1>Contact</h1>\n"
            '    {{ component("Counter", 10) }}\n'
            "  </section>\n"
            "</template>\n"
            "{% endblock %}\n"
        )

    # Register Counter Component
    from asok.component import Component

    class Counter(Component):
        count: int = 0

        def render(self):
            return '<button ws-click="increment"><span>{{ count }}</span></button>'

        def increment(self):
            self.count += 1

    app = Asok(root_dir=tmp_dir)
    app.config["DEBUG"] = True
    app.config["SECRET_KEY"] = "secret-router-test-key-12345"
    app.config["DATABASE"] = ":memory:"

    client = TestClient(app)

    # 1. Full-page load on root "/"
    res = client.get("/")
    assert res.status_code == 200
    # Assets injection check: router, alive, and directives in page
    assert "AsokRouter" in res.text or "asok-route" in res.text
    assert "asokWS" in res.text or "data-asok-component" in res.text
    assert "AsokDirectives" in res.text or "window.__asok_registry" in res.text
    # Both routes exist as template tags in DOM
    assert 'asok-route="/"' in res.text
    assert 'asok-route="/contact"' in res.text
    assert 'data-asok-component="Counter"' in res.text

    # 2. Check that asok_alive.js has leaveComponent and hooks window.Asok.init
    alive_asset = app.get_asset("asok_alive.js")
    assert "leaveComponent" in alive_asset
    assert "window.Asok.init" in alive_asset
    assert "asok:routechange" in alive_asset

    # 3. Check that asok_router.js cleans up components on route unmount
    router_asset = app.get_asset("asok_router.js")
    assert "leaveComponent" in router_asset
