def test_ui_directives_detection():
    from asok.request.template import TemplateMixin

    assert "asok-copy" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-modal" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-modal-open" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-modal-close" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-scroll-top" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-scroll-to" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-char-count" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-tabs" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-tab" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-tab-panel" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-progress" in TemplateMixin._ASOK_DIRECTIVES


def test_ui_directives_asset_injector_markers():
    from asok.core._asset_injector import _DIRECTIVE_MARKERS

    assert "asok-copy" in _DIRECTIVE_MARKERS
    assert "asok-modal" in _DIRECTIVE_MARKERS
    assert "asok-modal-open" in _DIRECTIVE_MARKERS
    assert "asok-modal-close" in _DIRECTIVE_MARKERS
    assert "asok-scroll-top" in _DIRECTIVE_MARKERS
    assert "asok-scroll-to" in _DIRECTIVE_MARKERS
    assert "asok-char-count" in _DIRECTIVE_MARKERS
    assert "asok-tabs" in _DIRECTIVE_MARKERS
    assert "asok-tab" in _DIRECTIVE_MARKERS
    assert "asok-tab-panel" in _DIRECTIVE_MARKERS
    assert "asok-progress" in _DIRECTIVE_MARKERS

