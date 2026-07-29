"""Tests for Asok SPA JavaScript asset and directive support."""

from asok.core import Asok, Request


def test_asok_spa_js_file_contains_asok_directives():
    """Verify that asok_spa.js contains support for asok- directives and attribute lookup helper."""
    js = Asok._read_asset_file("asok_spa.js")

    assert "function getAttr" in js
    assert "function hasAttr" in js
    assert "'asok-' + name" in js
    assert "'data-' + name" in js
    assert "if (mode === 'append') mode = 'beforeend';" in js
    assert "if (mode === 'prepend') mode = 'afterbegin';" in js


def test_asset_injector_triggers_on_asok_swap():
    """Verify that templates containing asok-swap or asok-block automatically trigger SPA asset injection."""
    app = Asok()
    req = Request({"REQUEST_METHOD": "GET", "PATH_INFO": "/"})
    html = '<button asok-block="counter" asok-swap="outerHTML" asok-url="/increment">+1</button>'

    injected = app._inject_assets(html, req, nonce="testnonce")
    assert "getAttr" in injected or "initSpaDirectives" in injected
