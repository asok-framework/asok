"""Test for 2FA template compilation bug."""

from asok.templates import render_template_string


def test_2fa_string_in_function_call():
    """Test that strings containing 'is' keyword don't break template compilation."""
    # This should not be parsed as an 'is' test
    template = "{{ t('2FA is Enabled') }}"
    context = {"t": lambda x: x}
    result = render_template_string(template, context)
    assert result == "2FA is Enabled"


def test_other_is_strings():
    """Test other strings that contain 'is' keyword."""
    template = "{{ message('This is a test') }}"
    context = {"message": lambda x: x}
    result = render_template_string(template, context)
    assert result == "This is a test"

    template = "{{ t('Password is required') }}"
    context = {"t": lambda x: x}
    result = render_template_string(template, context)
    assert result == "Password is required"


def test_actual_is_test_still_works():
    """Ensure real 'is' tests still work after the fix."""
    template = "{{ 'yes' if user is defined else 'no' }}"

    # With user defined
    result = render_template_string(template, {"user": "Alice"})
    assert result == "yes"

    # Without user defined
    result = render_template_string(template, {})
    assert result == "no"
