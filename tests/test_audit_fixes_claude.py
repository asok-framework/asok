"""
Tests validating fixes for all 9 findings from the comprehensive code review audit:
1. GraphQL query allowed by default for read operations.
2. MagicLink respects app.config['MAGIC_LINK_ALLOW_IN_MEMORY'].
3. .env line overrides existing os.environ without setdefault suppression.
4. GraphQL subscription fail-closed when GRAPHQL_AUTHORIZE is active but request is None.
5. Live components mask internal details in production to prevent method enumeration oracle.
6. Admin login catches ValueError without unhandled 500.
7. WebSocket server allows missing Origin when configured with ALLOW_WEBSOCKET_NO_ORIGIN.
8. Query.latest() supports fallback_to_id for safe dynamic queries.
9. Model.update() ignores CSRF tokens and supports update_safe() for unmapped form fields.
"""

import os
from unittest.mock import MagicMock

import pytest

from asok import Asok
from asok.api.graphql import _is_query_denied, _is_subscribe_denied
from asok.auth import MagicLink
from asok.orm import Field, Model
from asok.ws.live import _notify_live_error
from asok.ws.server import WebSocketServer


class AuditItem(Model):
    title = Field.String()
    score = Field.Integer(default=0)


def test_audit_1_graphql_query_allowed_by_default():
    app = Asok()
    # By default, read queries must be permitted (True)
    assert app.config["GRAPHQL_ALLOW_UNAUTHENTICATED_QUERIES"] is True
    assert _is_query_denied(app, ["{", "items", "}"]) is False


def test_audit_2_magiclink_respects_app_config(monkeypatch):
    monkeypatch.setenv("ASOK_ENV", "production")
    monkeypatch.delenv("MAGIC_LINK_ALLOW_IN_MEMORY", raising=False)

    app = Asok()
    app.config["MAGIC_LINK_ALLOW_IN_MEMORY"] = True

    req = MagicMock()
    req.environ = {"asok.app": app}
    req._sign = MagicMock(return_value="signed.token")

    # Should not raise RuntimeError because app.config has allowed it
    token = MagicLink.create_token(req, "test@example.com")
    assert token == "signed.token"


def test_audit_3_env_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("AUDIT_TEST_KEY", "stale_docker_value")
    # With override=True, it overrides stale environment variables
    Asok._apply_env_line("AUDIT_TEST_KEY=new_project_value", override=True)
    assert os.environ["AUDIT_TEST_KEY"] == "new_project_value"

    # With ASOK_ENV_OVERRIDE=true, it overrides automatically
    monkeypatch.setenv("ASOK_ENV_OVERRIDE", "true")
    monkeypatch.setenv("AUDIT_TEST_KEY_2", "stale_docker_value")
    Asok._apply_env_line("AUDIT_TEST_KEY_2=new_project_value")
    assert os.environ["AUDIT_TEST_KEY_2"] == "new_project_value"


def test_audit_4_graphql_subscription_fail_closed_when_request_none():
    app = Asok()
    app.config["GRAPHQL_AUTHORIZE"] = lambda r: True

    conn = MagicMock()
    conn.server.app = app
    conn.request = None

    # Must be denied (True) to prevent unauthenticated access
    assert _is_subscribe_denied(conn) is True


def test_audit_5_live_error_masks_internal_details_in_production(monkeypatch):
    monkeypatch.setenv("ASOK_ENV", "production")
    monkeypatch.delenv("DEBUG", raising=False)

    conn = MagicMock()
    err = PermissionError(
        "Method 'admin_wipe' on component 'Dashboard' is not decorated with @exposed"
    )
    _notify_live_error(conn, "c1", err)

    assert conn.send_json.called
    payload = conn.send_json.call_args[0][0]
    assert payload["op"] == "error"
    assert "admin_wipe" not in payload["message"]
    assert "Dashboard" not in payload["message"]
    assert payload["message"] == "Action not permitted or method not found"

    # Verify KeyError and ValueError also mask component IDs and class details
    conn.reset_mock()
    _notify_live_error(
        conn, "secret_cid_123", KeyError("Live component 'secret_cid_123' not found")
    )
    payload = conn.send_json.call_args[0][0]
    assert "secret_cid_123" not in payload["message"]
    assert payload["message"] == "Invalid component request or component not found"

    conn.reset_mock()
    _notify_live_error(
        conn,
        "secret_cid_123",
        ValueError("Failed to restore component 'UserPanel' (cid='secret_cid_123')"),
    )
    payload = conn.send_json.call_args[0][0]
    assert "UserPanel" not in payload["message"]
    assert "secret_cid_123" not in payload["message"]
    assert payload["message"] == "Invalid component request or component not found"


def test_audit_6_admin_login_handles_value_error_gracefully():
    from asok.admin.views.auth import AuthViewsMixin

    class DummyAdminAuth(AuthViewsMixin):
        def __init__(self):
            self.prefix = "/admin"
            self.logger = MagicMock()
            self._admin_logs = []

        def t(self, req, text, **kwargs):
            return text

    handler = DummyAdminAuth()
    req = MagicMock()
    req.authenticate = MagicMock(
        side_effect=ValueError("Authentication model not configured")
    )
    req.flash = MagicMock()

    form = MagicMock()
    form.validate.return_value = True
    form.email.value = "admin@example.com"
    form.password.value = "secret"

    # Should catch ValueError and not crash with unhandled 500
    handler._process_login_attempt(req, form)
    req.flash.assert_called_with(
        "error", "Authentication service is currently unavailable."
    )


def test_audit_7_websocket_missing_origin_opt_in(monkeypatch):
    monkeypatch.setenv("ASOK_ENV", "production")
    monkeypatch.delenv("DEBUG", raising=False)

    app = Asok()
    app.config["ALLOW_WEBSOCKET_NO_ORIGIN"] = True

    server = WebSocketServer(app=app, port=9999)
    # With ALLOW_WEBSOCKET_NO_ORIGIN, programmatic clients without Origin header are accepted
    assert server._is_origin_allowed(None) is True


def test_audit_8_query_latest_fallback_to_id(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit8.db")
    AuditItem.close_connections()
    monkeypatch.setattr(AuditItem, "_db_path", db_path)
    AuditItem.create_table()

    # Calling latest on non-existent column with fallback_to_id does not raise ValueError
    q = AuditItem.query().latest("non_existent_column", fallback_to_id=True)
    assert q._order == "id DESC"


def test_audit_9_model_update_safe_and_csrf_bypass(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit9.db")
    AuditItem.close_connections()
    monkeypatch.setattr(AuditItem, "_db_path", db_path)
    AuditItem.create_table()

    item = AuditItem.create(title="Original", score=10)

    # 1. CSRF tokens are ignored silently even in default update()
    item.update(title="Updated Title", _csrf="csrf_token_xyz")
    assert item.title == "Updated Title"

    # 2. update_safe ignores arbitrary form/extra fields without AttributeError
    item.update_safe(title="Safe Update", extra_ui_flag=True, submit_action="save")
    assert item.title == "Safe Update"

    # 3. Direct typo without update_safe still raises AttributeError for strictness
    with pytest.raises(AttributeError):
        item.update(score_typo=20)


def test_tenant_middleware_allowed_tenants_env(monkeypatch):
    from asok.middleware.tenant import TenantMiddleware

    monkeypatch.setenv("ASOK_ALLOWED_TENANTS", "acme,corp")
    mw = TenantMiddleware()
    assert mw.allowed_tenants == {"acme", "corp"}
    assert mw._validate_tenant("acme") is True
    assert mw._validate_tenant("corp") is True
    assert mw._validate_tenant("evil_tenant") is False


def test_docs_authorize_protects_html_and_json_docs():
    from asok.api import handle_docs_request
    from asok.exceptions import ForbiddenError
    from asok.request import Request

    app = Asok()
    app.config["DOCS_PATH"] = "/docs"
    app.config["OPENAPI_PATH"] = "/openapi.json"
    # Authorize only admin users
    app.config["DOCS_AUTHORIZE"] = lambda req: bool(
        getattr(req, "user", None) and getattr(req.user, "is_admin", False)
    )

    # Mock OpenAPI paths to ensure _should_serve_docs returns true
    from unittest.mock import patch

    with patch("asok.api._should_serve_docs", return_value={"paths": {"/test": {}}}):
        # 1. Non-admin user accessing /openapi.json receives 403 JSON
        import json

        req_json = Request(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/openapi.json",
                "wsgi.url_scheme": "http",
                "HTTP_HOST": "localhost",
            }
        )
        req_json.user = MagicMock(is_admin=False)
        res_json = handle_docs_request(app, req_json)
        assert req_json.status == "403 Forbidden"
        assert json.loads(res_json)["error"] == "Unauthorized"

        # 2. Non-admin user accessing /docs raises ForbiddenError (403)
        req_html = Request(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/docs",
                "wsgi.url_scheme": "http",
                "HTTP_HOST": "localhost",
            }
        )
        req_html.user = MagicMock(is_admin=False)
        with pytest.raises(ForbiddenError):
            handle_docs_request(app, req_html)

        # 3. Admin user accessing /openapi.json receives 200 spec
        req_admin = Request(
            {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/openapi.json",
                "wsgi.url_scheme": "http",
                "HTTP_HOST": "localhost",
            }
        )
        req_admin.user = MagicMock(is_admin=True)
        res_admin = handle_docs_request(app, req_admin)
        assert json.loads(res_admin) == {"paths": {"/test": {}}}
