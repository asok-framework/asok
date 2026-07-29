def test_dismiss_directive_detection():
    from asok.request.template import TemplateMixin

    assert "asok-dismiss" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-dismiss-trigger" in TemplateMixin._ASOK_DIRECTIVES


def test_dismiss_asset_injector_markers():
    from asok.core._asset_injector import _DIRECTIVE_MARKERS

    assert "asok-dismiss" in _DIRECTIVE_MARKERS
    assert "asok-dismiss-trigger" in _DIRECTIVE_MARKERS
