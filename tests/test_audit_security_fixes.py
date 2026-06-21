import time
from unittest.mock import MagicMock

import pytest

from asok.admin.views.auth import AuthViewsMixin
from asok.admin.views.crud import CRUDViewsMixin
from asok.core import Asok
from asok.orm import MODELS_REGISTRY, Model
from asok.request import Request


def test_ip_resolution_rightmost():
    """Verify that request.ip returns the rightmost element of X-Forwarded-For when proxy is trusted."""
    app = Asok()
    app.config["TRUSTED_PROXIES"] = "*"

    # Case 1: multiple IPs in X-Forwarded-For
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_X_FORWARDED_FOR": "1.2.3.4, 5.6.7.8",
        "asok.app": app,
    }
    request = Request(environ)
    assert request.ip == "5.6.7.8"

    # Case 2: single IP in X-Forwarded-For
    environ2 = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_X_FORWARDED_FOR": "9.10.11.12",
        "asok.app": app,
    }
    request2 = Request(environ2)
    assert request2.ip == "9.10.11.12"

    # Case 3: no X-Forwarded-For
    environ3 = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "REMOTE_ADDR": "192.168.1.1",
        "asok.app": app,
    }
    request3 = Request(environ3)
    assert request3.ip == "192.168.1.1"


def test_impersonation_session_regeneration():
    """Verify that session is regenerated during impersonation start and stop."""
    # Mock Request with Session and session_regenerate method
    request = MagicMock()
    request.session = {
        "user_id": 1,
    }

    admin_user = MagicMock()
    admin_user.id = 1

    target = MagicMock()
    target.id = 2
    target.name = "Target User"

    auth_view = AuthViewsMixin()
    auth_view.prefix = "/admin"
    auth_view.t = MagicMock(return_value="Now acting as Target User")
    auth_view._log = MagicMock()

    # 1. Impersonate start
    from asok.exceptions import RedirectException

    with pytest.raises(RedirectException):
        auth_view._setup_impersonation(request, admin_user, target, "User")

    assert request.session["user_id"] == 2
    assert request.session["impersonator_id"] == 1
    request.session_regenerate.assert_called_once()

    # 2. Impersonate stop
    request.session = {
        "user_id": 2,
        "impersonator_id": 1,
        "impersonate_started_at": time.time(),
    }
    request.session_regenerate.reset_mock()

    with pytest.raises(RedirectException):
        auth_view._stop_impersonate(request)

    assert request.session["user_id"] == 1
    assert "impersonator_id" not in request.session
    assert "impersonate_started_at" not in request.session
    request.session_regenerate.assert_called_once()


def test_csv_formula_injection_sanitization():
    """Verify that cell values starting with formula characters are sanitized with a single quote prefix."""
    crud = CRUDViewsMixin()

    # Define a mock model and object
    class DummyField:
        pass

    class DummyModel(Model):
        _fields = {
            "name": DummyField(),
            "formula": DummyField(),
            "normal": DummyField(),
        }

    MODELS_REGISTRY["DummyModel"] = DummyModel

    # Test cases: (value, expected_csv_value)
    cases = [
        ("=1+1", "'=1+1"),
        ("+1-2", "'+1-2"),
        ("-hello", "'-hello"),
        ("@formula", "'@formula"),
        ("\tvalue", "'\tvalue"),
        ("\rvalue", "'\rvalue"),
        ("normal_text", "normal_text"),
        ("123", "123"),
        ("", ""),
        (None, ""),
    ]

    for val, expected in cases:
        it = DummyModel()
        it.normal = val
        assert crud._get_csv_cell_value(it, "normal", DummyModel) == expected
