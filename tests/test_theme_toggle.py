def test_theme_toggle_directive_detection():
    from asok.request.template import TemplateMixin

    assert "asok-theme-toggle" in TemplateMixin._ASOK_DIRECTIVES
    assert "data-theme-toggle" in TemplateMixin._ASOK_DIRECTIVES
    assert "data-asok-theme-toggle" in TemplateMixin._ASOK_DIRECTIVES


def test_theme_toggle_asset_injector_markers():
    from asok.core._asset_injector import _DIRECTIVE_MARKERS

    assert "asok-theme-toggle" in _DIRECTIVE_MARKERS
    assert "data-theme-toggle" in _DIRECTIVE_MARKERS
    assert "data-asok-theme-toggle" in _DIRECTIVE_MARKERS


def test_anti_fouc_theme_script_injected():
    from unittest.mock import MagicMock

    from asok.core._asset_injector import AssetInjector

    app = MagicMock()
    app.config = {}

    req = MagicMock()
    req.environ = {}
    req._asok_anti_fouc_done = False
    req._asok_pending_styles = ""
    req._asok_pending_scripts = ""
    req.nonce = "test-nonce"

    html = '<!DOCTYPE html><html><head></head><body><button asok-theme-toggle>Toggle</button></body></html>'
    injector = AssetInjector(
        app, req, html, nonce="test-nonce", stream=False, include_scripts=True, only_scripts=False
    )
    result = injector.inject()

    assert 'localStorage.getItem(\'asok-theme\')' in result or 'asok-theme' in result
    assert 'data-theme' in result
