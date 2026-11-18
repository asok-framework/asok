"""Regression tests for security audit fixes."""

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from asok import Asok, Request
from asok.admin import models as admin_models
from asok.api.graphql import GraphQLParser, check_complexity, tokenize
from asok.api.versioning import get_request_version
from asok.auth import BearerToken, MagicLink
from asok.cache import default_cache
from asok.component import Component
from asok.core._expr_validator import is_safe_expression
from asok.core.routing import RoutingMixin
from asok.exceptions import SecurityError
from asok.middleware.tenant import TenantMiddleware
from asok.orm.field import Field
from asok.orm.model import Model
from asok.orm.query import Query
from asok.orm.tenant import TenantRouter, clear_tenant_databases, tenant_context
from asok.templates.filters import _fallback_markdown_links, _markdown_filter
from asok.templates.sandbox import _get
from asok.utils.security import request_authority
from asok.utils.svg_sanitizer import sanitize_svg
from asok.ws.server import _is_debug_env


def _make_request(
    path="/",
    method="GET",
    headers=None,
    query="",
    scheme="http",
    app=None,
):
    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "wsgi.url_scheme": scheme,
        "HTTP_HOST": "example.com",
    }
    if app is not None:
        environ["asok.app"] = app
    for k, v in (headers or {}).items():
        environ["HTTP_" + k.upper().replace("-", "_")] = v
    return Request(environ)


def test_api_version_blocks_path_traversal():
    req_bad = _make_request("/api/users", headers={"X-API-Version": "../../partials"})
    assert get_request_version(req_bad) is None
    req_abs = _make_request("/api/users", headers={"X-API-Version": "/tmp/evil"})
    assert get_request_version(req_abs) is None
    req_ok = _make_request("/api/users", headers={"X-API-Version": "v2.1"})
    assert get_request_version(req_ok) == "v2.1"

    assert RoutingMixin._is_unsafe_segment("../secret") is True
    assert RoutingMixin._is_unsafe_segment("a/b") is True
    assert RoutingMixin._is_unsafe_segment("users") is False


def test_lang_redirect_url_blocks_protocol_relative_open_redirect():
    app = Asok()
    req = _make_request("//evil.com/path", query="lang=fr&tab=1", app=app)
    assert app._lang_redirect_url(req) == "/evil.com/path?tab=1"

    req_bs = _make_request("/\\evil.com", query="lang=en", app=app)
    assert app._lang_redirect_url(req_bs) == "/evil.com"


def test_global_rate_limit_applies_to_specialized_handlers():
    app = Asok()
    app.config["RATE_LIMIT"] = True
    app.config["RATE_LIMIT_PER_MINUTE"] = 2

    statuses = []

    def start_response(status, headers, exc_info=None):
        statuses.append(status)

    with patch.object(default_cache, "incr", side_effect=[1, 2, 3]):
        for _ in range(3):
            req = _make_request("/admin/login", app=app)
            req.environ["REMOTE_ADDR"] = "10.99.99.1"
            app._run_specialized_handlers(req, req.environ, start_response)

    assert "429 Too Many Requests" in statuses


def test_trusted_proxies_env_override_parses_list():
    with patch.dict(os.environ, {"ASOK_TRUSTED_PROXIES": "10.0.0.1, 10.0.0.2"}):
        app = Asok()
        assert app.config["TRUSTED_PROXIES"] == ["10.0.0.1", "10.0.0.2"]
        req_ok = _make_request("/", app=app)
        req_ok.environ["REMOTE_ADDR"] = "10.0.0.1"
        assert req_ok._is_proxy_trusted("10.0.0.1") is True
        assert req_ok._is_proxy_trusted("10.0.0") is False


def test_cors_preflight_does_not_reflect_wildcard_for_untrusted_origin():
    app = Asok()
    app.config["CORS_ORIGINS"] = ["https://trusted.example"]
    app.config["DEBUG"] = False
    headers = dict(app._preflight_cors_headers("https://evil.example", app.config["CORS_ORIGINS"]))
    assert "Access-Control-Allow-Origin" not in headers

    ok_headers = dict(app._preflight_cors_headers("https://trusted.example", app.config["CORS_ORIGINS"]))
    assert ok_headers["Access-Control-Allow-Origin"] == "https://trusted.example"
    assert ok_headers["Vary"] == "Origin"


def test_multitenancy_subdomain_precedence_fail_closed_and_cache_isolation():
    mw = TenantMiddleware(extract_subdomain=True, base_domain="app.com")
    req = SimpleNamespace(
        headers={"X-Tenant-ID": "tenant_b"},
        args={"tenant": "tenant_b"},
        host="tenant_a.app.com",
    )
    assert mw.extract_tenant(req) == "tenant_a"

    clear_tenant_databases()
    router = TenantRouter()
    with tenant_context("unconfigured_tenant"):
        with pytest.raises(ValueError, match="Unknown or unconfigured tenant"):
            router.db_for_read(Model)

    q = Query(Model)
    with tenant_context("t1"):
        k1 = q._resolve_cache_key("SELECT 1", [])
    with tenant_context("t2"):
        k2 = q._resolve_cache_key("SELECT 1", [])
    assert k1 != k2


def test_ssg_lang_validation_tenant_isolation_and_private_cache_with_cookie():
    app = Asok()
    app.config["LOCALES"] = ["fr", "en"]
    app.config["LOCALE"] = "fr"

    req_bad = _make_request("/about", app=app)
    req_bad.lang = "../../etc/passwd"
    assert app._lang_for_request(req_bad) == "fr"

    with tenant_context("acme"):
        path = app._get_ssg_cache_file("/about", "fr")
        assert "_tenant_acme" in path

    captured_headers = []
    req_new = _make_request("/about", app=app)
    req_new.environ["asok.csrf_token_new"] = "tok1234567890"
    app._build_and_send_generated_response(
        req_new,
        "<html><body>ok</body></html>",
        60,
        lambda s, h: captured_headers.extend(h),
    )
    header_map = dict(captured_headers)
    assert header_map["Cache-Control"].startswith("private")


def test_csrf_delete_origin_check_and_safe_redirects():
    app = Asok()
    app.config["SECRET_KEY"] = "x" * 32
    req_del = _make_request(
        "/api/item/1",
        method="DELETE",
        headers={"Cookie": "asok_csrf=abcdef123456"},
        app=app,
    )
    with pytest.raises(SecurityError):
        req_del.validate({"id": "optional"})

    req_origin = _make_request(
        "/submit",
        method="POST",
        headers={
            "Origin": "https://evil.com",
            "Cookie": "asok_csrf=abcdef123456",
            "X-CSRF-Token": "abcdef123456",
        },
        scheme="http",
        app=app,
    )
    with pytest.raises(SecurityError):
        req_origin.verify_csrf()

    req_next = _make_request("/\\evil.com", app=app)
    assert req_next._safe_next_url() == "/"

    app.config["ALLOWED_HOSTS"] = ["good.example"]
    req_host = _make_request("/", headers={"Host": "evil.example"}, app=app)
    assert request_authority(req_host) == "good.example"


def test_markdown_filter_sanitizes_xss():
    out_fallback = str(_fallback_markdown_links('<script>alert(1)</script> [click](javascript:alert(1))'))
    assert "<script>" not in out_fallback
    assert "javascript:" not in out_fallback

    out_md = str(_markdown_filter('<script>alert(1)</script> <img src=x onerror=alert(1)> [x](javascript:alert(1))'))
    assert "<script" not in out_md
    assert "javascript:" not in out_md
    if "<img" in out_md:
        assert "onerror" not in out_md


def test_expr_validator_blocks_subscript_and_cookie_bypasses():
    assert is_safe_expression("self['evalx'.slice(0, 4)]('alert(1)')") is False
    assert is_safe_expression("self[['ev', 'al'].join('')]('1')") is False
    assert is_safe_expression("document['cookie']") is False
    assert is_safe_expression("count + 1") is True


def test_template_sandbox_blocks_format():
    assert _get("hello {}", "format") == ""
    assert _get("hello {x}", "format_map") == ""


def test_component_cross_class_state_substitution_blocked():
    class CompA(Component):
        role: str = "user"

    class CompB(Component):
        role: str = "admin"

    a = CompA(_cid="c1")
    signed = a._sign_state("secret" * 6)
    assert CompB._from_signed_state(signed, "secret" * 6) is None
    assert CompA._from_signed_state(signed, "secret" * 6) is not None


def test_ws_debug_env_fail_closed():
    with patch.dict(os.environ, {}, clear=True):
        assert _is_debug_env() is False


def test_graphql_fragment_bomb_and_negative_limit_blocked():
    fragments = "\n".join(
        f"fragment F{i} on User {{ ...F{i+1} ...F{i+1} }}" for i in range(12)
    ) + "\nfragment F12 on User { id }"
    query = f"{{ users {{ ...F0 }} }} {fragments}"
    parser = GraphQLParser(tokenize(query))
    with pytest.raises(ValueError, match="maximum expanded field count"):
        parser.parse()

    fields = GraphQLParser(tokenize("{ users(limit: -1) { id name } }")).parse()
    with pytest.raises(ValueError, match="limit"):
        check_complexity(fields)


def test_svg_sanitizer_blocks_set_href_and_css_escapes():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><use><set attributeName="href" to="javascript:alert(1)" /></use><rect style="fill: \\6a avascript:alert(1)" /></svg>'
    cleaned = sanitize_svg(svg).decode("utf-8")
    assert "attributename" not in cleaned.lower()
    assert "\\6a" not in cleaned


def test_magic_link_and_bearer_domain_separation_and_atomic_replay():
    app = Asok()
    app.config["SECRET_KEY"] = "s" * 32
    req = _make_request("/", app=app)
    bearer_tok = BearerToken.create(req, user_id="alice@example.com", expires_in=300)
    assert MagicLink.verify_token(req, bearer_tok) is None

    magic_tok = MagicLink.create_token(req, email="alice@example.com", expires_in=300)
    assert BearerToken.verify(req, magic_tok) is None
    with patch.object(default_cache, "incr", side_effect=[1, 2]):
        assert MagicLink.verify_token(req, magic_tok) == "alice@example.com"
        assert MagicLink.verify_token(req, magic_tok) is None


def test_role_permissions_protected_and_admin_bulk_field_guard():
    assert 'permissions = Field.String(default="", protected=True)' in admin_models._DEFAULT_ROLE_MODEL_SRC
    from asok.admin import Admin

    admin = Admin(app=Asok())
    entry = {
        "slug": "roles",
        "readonly_fields": ["created_at"],
        "form_exclude": ["secret_note"],
    }
    req = MagicMock()
    req.user = SimpleNamespace(is_admin=False, roles=[])
    assert admin._is_bulk_field_allowed(req, entry, Field.String(default="", protected=True), "permissions") is False
    assert admin._is_bulk_field_allowed(req, entry, Field.String(), "name") is True


def test_core_js_assets_security_hardening():
    directives_js = Asok._read_asset_file("asok_directives.js")
    assert "url.href" in directives_js
    assert "isSameOrigin(action)" in directives_js
    assert "assignSafeStore" in directives_js
    assert "'__proto__'" in directives_js
    assert "'data'" in directives_js
    assert "encodeURIComponent" in directives_js

    directives_min = Asok._read_asset_file("asok_directives.min.js")
    assert ".href" in directives_min
    assert '"__proto__"' in directives_min
    assert '"data"' in directives_min
    assert "encodeURIComponent" in directives_min

    sec_js = Asok._read_asset_file("asok_security_utils.js")
    assert "'data'" in sec_js

    for asset in ("asok_spa.js", "asok_spa.min.js", "asok_router.js", "asok_router.min.js"):
        js = Asok._read_asset_file(asset)
        assert "querySelector('script[nonce]')" not in js
        assert 'querySelector("script[nonce]")' not in js

    alive_js = Asok._read_asset_file("asok_alive.js")
    assert "script.nonce || script.getAttribute('nonce')" in alive_js

    spa_js = Asok._read_asset_file("asok_spa.js")
    assert "isSameOrigin(sseUrl)" in spa_js
    assert "isSameOrigin(url)" in spa_js

    widgets_js = Asok._read_asset_file("asok_widgets.js")
    assert "escDiv.textContent" in widgets_js


def test_admin_bulk_custom_action_requires_edit_permission():
    from asok.admin import Admin

    admin = Admin(app=Asok())
    req = MagicMock()
    req.user = SimpleNamespace(id=2, is_admin=False, roles=[SimpleNamespace(permissions="posts.view")])
    req.user.can = lambda perm: perm == "posts.view"
    req.lang = "en"
    req.csrf_input = lambda: ""
    req.get_flashed_messages = lambda: []
    entry = {"slug": "posts", "model": MagicMock(), "can_edit": True, "actions": ["publish"]}
    res = admin._dispatch_bulk_action(req, entry, [1], "publish")
    assert isinstance(res, str)
    req.status_code.assert_called_with(403)
    entry["model"].publish.assert_not_called()


def test_toolbar_escapes_inline_json_and_redacts_secrets(tmp_path):
    from asok.toolbar import DeveloperToolbar

    app = Asok()
    app.config["DATABASE"] = "postgresql://dbuser:supersecret@db.internal:5432/app"
    req = _make_request(
        "/test?q=</script><img+src=x+onerror=alert(1)>",
        headers={"Cookie": "asok_sid=secret_cookie_val"},
        app=app,
    )
    req.args["q"] = "</script><script>alert(1)</script>"
    tb = DeveloperToolbar(req, app)
    rendered = tb.render()
    assert "</script><script>alert(1)</script>" not in rendered
    assert "\\u003c/script\\u003e" in rendered
    assert "supersecret" not in rendered
    assert "secret_cookie_val" not in rendered


def test_build_production_env_generates_random_secret_and_safe_hosts(tmp_path):
    from asok.cli.build import _write_production_env

    _write_production_env(str(tmp_path))
    content = (tmp_path / ".env.production").read_text()
    assert "change-me-to-a-very-secure" not in content
    assert "ALLOWED_HOSTS=*" not in content
    assert "SECRET_KEY=" in content


def test_scheduler_distributed_lock_fails_closed_on_redis_error():
    from asok.scheduler import ScheduledTask

    task = ScheduledTask(60, lambda: None)
    with patch.dict(os.environ, {"ASOK_QUEUE_BACKEND": "redis"}):
        with patch("asok.scheduler._get_scheduler_redis_client", side_effect=RuntimeError("redis down")):
            assert task._acquire_distributed_lock() is False
