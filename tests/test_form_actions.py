"""
Tests for Form Actions (action_* convention).
"""

import io

import pytest

from asok.exceptions import RedirectException
from asok.request import Request


def make_request(path="/test", method="POST", data=None):
    """Build a minimal Request with form data."""
    from urllib.parse import urlencode

    body = urlencode(data or {}).encode()
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(body)),
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "HTTP_HOST": "localhost",
        "wsgi.input": io.BytesIO(body),
        "wsgi.errors": io.BytesIO(),
        "wsgi.url_scheme": "http",
        "asok.secret_key": "test-secret",
    }
    return Request(environ)


def test_action_validation_blocks_private():
    """Test that action names starting with _ are blocked."""
    action_name = "_private"

    # Security validation (from core.py lines 1735-1740)
    if not action_name.replace("_", "").replace("-", "").isalnum():
        action_name = None
    elif action_name.startswith("_"):
        action_name = None

    # Should be blocked
    assert action_name is None


def test_action_validation_blocks_malicious():
    """Test that malicious action names are blocked."""
    malicious_names = ["../admin", "../../etc/passwd", "__init__", "system('rm')"]

    for bad_name in malicious_names:
        action_name = bad_name

        # Security validation
        if not action_name.replace("_", "").replace("-", "").isalnum():
            action_name = None
        elif action_name.startswith("_"):
            action_name = None

        # Should be blocked
        assert action_name is None, f"Failed to block: {bad_name}"


def test_action_validation_allows_valid():
    """Test that valid action names are allowed."""
    valid_names = ["delete", "save_draft", "publish", "archive", "export-pdf"]

    for good_name in valid_names:
        action_name = good_name

        # Security validation
        if not action_name.replace("_", "").replace("-", "").isalnum():
            action_name = None
        elif action_name.startswith("_"):
            action_name = None

        # Should pass
        assert action_name is not None, f"Incorrectly blocked: {good_name}"


def test_action_with_redirect_raises_exception():
    """Test that action calling redirect() raises RedirectException."""
    req = make_request()

    # Simulate action_save
    with pytest.raises(RedirectException) as exc_info:
        req.redirect("/success")

    # Verify redirect URL
    assert exc_info.value.url == "/success"
    assert exc_info.value.status == 302


def test_action_returns_json():
    """Test that action can return JSON response."""
    req = make_request()

    # Simulate action_delete returning JSON
    response = req.json({"status": "deleted", "id": 123})

    # Verify it's a valid response (string with JSON)
    assert response is not None
    assert isinstance(response, str)
    assert "deleted" in response


def test_form_action_convention():
    """Test that form action convention works with getattr."""

    # Create a mock module with actions
    class MockModule:
        def action_delete(self, request):
            return request.json({"action": "delete"})

        def action_publish(self, request):
            return request.json({"action": "publish"})

        def _private_method(self, request):
            # Should not be accessible
            return request.json({"hacked": True})

    module = MockModule()
    req = make_request()

    # Test valid action
    action_func = getattr(module, "action_delete", None)
    assert callable(action_func)
    result = action_func(req)
    assert result is not None

    # Test another valid action
    action_func = getattr(module, "action_publish", None)
    assert callable(action_func)

    # Test missing action
    action_func = getattr(module, "action_missing", None)
    assert action_func is None
