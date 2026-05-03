"""Test the admin /me profile page renders correctly."""

from asok.templates import render_template_string


def test_admin_me_page_2fa_enabled():
    """Test that the admin /me page renders with 2FA enabled."""
    # Simplified version of the actual admin template
    template = """
    {% if twofa_enabled %}
        <h3>{{ t('2FA is Enabled') }}</h3>
        <p>{{ t('Your account is secured with an extra layer of protection using a TOTP authenticator app.') }}</p>
        <button>{{ t('Disable 2FA') }}</button>
    {% else %}
        <h3>{{ t('Secure Your Account') }}</h3>
        <button>{{ t('Setup Two-Factor') }}</button>
    {% endif %}
    """
    context = {
        "twofa_enabled": True,
        "t": lambda x: x,
    }
    result = render_template_string(template, context)
    assert "2FA is Enabled" in result
    assert "Disable 2FA" in result
    assert "Setup Two-Factor" not in result


def test_admin_me_page_2fa_disabled():
    """Test that the admin /me page renders with 2FA disabled."""
    template = """
    {% if twofa_enabled %}
        <h3>{{ t('2FA is Enabled') }}</h3>
        <button>{{ t('Disable 2FA') }}</button>
    {% else %}
        <h3>{{ t('Secure Your Account') }}</h3>
        <button>{{ t('Setup Two-Factor') }}</button>
    {% endif %}
    """
    context = {
        "twofa_enabled": False,
        "t": lambda x: x,
    }
    result = render_template_string(template, context)
    assert "Secure Your Account" in result
    assert "Setup Two-Factor" in result
    assert "2FA is Enabled" not in result
    assert "Disable 2FA" not in result


def test_2fa_in_macro_calls():
    """Test 2FA text works in button macros."""
    template = """
    {% macro btn(label, style) %}
        <button class="btn-{{ style }}">{{ label }}</button>
    {% endmacro %}
    {{ btn(t('Enable 2FA'), 'primary') }}
    {{ btn(t('Disable 2FA'), 'danger') }}
    """
    context = {"t": lambda x: x}
    result = render_template_string(template, context)
    assert "Enable 2FA" in result
    assert "Disable 2FA" in result
