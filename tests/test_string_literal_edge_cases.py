"""Test edge cases for string literals containing keywords."""

from asok.templates import render_template_string


def test_strings_with_is_keyword():
    """Test various strings containing 'is' keyword."""
    test_cases = [
        ("{{ msg('This is a test') }}", "This is a test"),
        ("{{ msg('User is admin') }}", "User is admin"),
        ("{{ msg('Password is required') }}", "Password is required"),
        ("{{ msg('2FA is Enabled') }}", "2FA is Enabled"),
        ("{{ msg('Session is active') }}", "Session is active"),
        ("{{ msg('Database is ready') }}", "Database is ready"),
        ("{{ msg('X is Y is Z') }}", "X is Y is Z"),  # Multiple 'is'
        ("{{ msg('What is this?') }}", "What is this?"),
    ]

    for template, expected in test_cases:
        context = {"msg": lambda x: x}
        result = render_template_string(template, context)
        assert result == expected, f"Failed for template: {template}"


def test_strings_with_numbers_and_is():
    """Test strings starting with numbers followed by 'is'."""
    test_cases = [
        ("{{ t('2FA is enabled') }}", "2FA is enabled"),
        ("{{ t('3D is cool') }}", "3D is cool"),
        ("{{ t('24/7 is available') }}", "24/7 is available"),
    ]

    for template, expected in test_cases:
        context = {"t": lambda x: x}
        result = render_template_string(template, context)
        assert result == expected, f"Failed for template: {template}"


def test_mixed_is_tests_and_strings():
    """Test templates with both real 'is' tests and strings containing 'is'."""
    template = """
    {% if user is defined %}
        {{ t('User is logged in') }}
    {% else %}
        {{ t('User is not found') }}
    {% endif %}
    """

    # With user defined
    context = {"user": "Alice", "t": lambda x: x}
    result = render_template_string(template, context)
    assert "User is logged in" in result
    assert "User is not found" not in result

    # Without user defined
    context = {"t": lambda x: x}
    result = render_template_string(template, context)
    assert "User is not found" in result
    assert "User is logged in" not in result


def test_nested_quotes():
    """Test strings with nested quotes containing 'is'."""
    template = """{{ msg("This is a 'test'") }}"""
    context = {"msg": lambda x: x}
    result = render_template_string(template, context)
    # HTML escaping converts ' to &#x27;
    assert result == "This is a &#x27;test&#x27;"

    template = """{{ msg('This is a "test"') }}"""
    context = {"msg": lambda x: x}
    result = render_template_string(template, context)
    # HTML escaping converts " to &quot;
    assert result == 'This is a &quot;test&quot;'


def test_escaped_quotes_in_strings():
    """Test strings with escaped quotes containing 'is'."""
    template = r"""{{ msg('This is a \'test\'') }}"""
    context = {"msg": lambda x: x}
    result = render_template_string(template, context)
    # HTML escaping converts ' to &#x27;
    assert result == "This is a &#x27;test&#x27;"


def test_multiline_strings_with_is():
    """Test that 'is' in multiline strings is not treated as a test."""
    # When template expressions are normalized, newlines become spaces
    template = """{{ t('This is
    a multiline string') }}"""
    context = {"t": lambda x: " ".join(x.split())}  # Normalize whitespace
    result = render_template_string(template, context)
    assert "This is a multiline string" in result
