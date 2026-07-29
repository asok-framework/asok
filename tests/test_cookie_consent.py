def test_cookie_consent_directive_detection():
    from asok.request.template import TemplateMixin

    assert "asok-cookie-banner" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-cookie-accept" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-cookie-reject" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-cookie-reset" in TemplateMixin._ASOK_DIRECTIVES
    assert "asok-cookie-script" in TemplateMixin._ASOK_DIRECTIVES


def test_cookie_consent_asset_injector_markers():
    from asok.core._asset_injector import _DIRECTIVE_MARKERS

    assert "asok-cookie-banner" in _DIRECTIVE_MARKERS
    assert "asok-cookie-accept" in _DIRECTIVE_MARKERS
    assert "asok-cookie-reject" in _DIRECTIVE_MARKERS
    assert "asok-cookie-reset" in _DIRECTIVE_MARKERS
    assert "asok-cookie-script" in _DIRECTIVE_MARKERS
