"""Fuzzing and security boundary tests for the JS expression validator.

Verifies that the parser differential and dynamic evaluation bypass vectors
are correctly blocked.
"""

from asok.core._expr_validator import is_safe_expression


def test_valid_safe_expressions():
    """Verify that common safe directive expressions are allowed."""
    assert is_safe_expression("items.filter(x => x.active)") is True
    assert is_safe_expression("items.map(x => x.name)") is True
    assert is_safe_expression("query === 'test'") is True
    assert is_safe_expression("time = new Date().toLocaleTimeString()") is True
    assert is_safe_expression("x = 1; y = 2;") is True
    assert is_safe_expression("value ? 'active' : 'inactive'") is True


def test_constructor_eval_bypasses():
    """Verify that constructor-based eval and reflection bypass vectors are blocked."""
    # 1. Plain attribute/bracket accesses to blocked properties
    assert is_safe_expression("this.constructor") is False
    assert is_safe_expression("this['constructor']") is False
    assert is_safe_expression("this.prototype") is False
    assert is_safe_expression("this['prototype']") is False
    assert is_safe_expression("Object.getPrototypeOf(x)") is False

    # 2. String-concatenation obfuscation inside subscripts
    assert (
        is_safe_expression(
            "this['con' + 'structor']['con' + 'structor']('console.log(1)')()"
        )
        is False
    )
    assert is_safe_expression("this['__cl' + 'ass__']") is False
    assert is_safe_expression("this['__glo' + 'bals__']") is False

    # 3. Dynamic Function constructor calls
    assert is_safe_expression("Function('console.log(1)')()") is False


def test_obfuscation_and_comments():
    """Verify that comments and spacing obfuscation are stripped and blocked."""
    assert is_safe_expression("/* comment */ eval('console.log(1)')") is False
    assert is_safe_expression("// comment\neval('console.log(1)')") is False
    assert is_safe_expression("window  .  location") is False


def test_unicode_whitespace_bypasses():
    """Verify that unicode whitespaces do not bypass the checks."""
    # Unicode non-breaking space (U+00A0)
    assert is_safe_expression("eval\u00a0('console.log(1)')") is False
    # Unicode line separator (U+2028)
    assert is_safe_expression("eval\u2028('console.log(1)')") is False


def test_strict_static_templates_enforcement():
    import pytest

    from asok.core.asok import Asok
    from asok.exceptions import SecurityError
    from asok.request import Request

    app = Asok()
    app.config["DEBUG"] = False
    app.config["STRICT_STATIC_TEMPLATES"] = True

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
    }
    request = Request(environ)
    content = '<div asok-state="{ open: true }"></div>'

    with pytest.raises(SecurityError, match="STRICT_STATIC_TEMPLATES is enabled"):
        app._inject_assets(content, request, "testnonce123")
