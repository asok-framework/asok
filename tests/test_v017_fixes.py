import os
import time

import pytest

from asok import Field, Model
from asok.admin import Admin
from asok.core import Asok
from asok.exceptions import RedirectException
from asok.request import Request
from asok.request.upload import UploadedFile
from asok.session import Session

# ---------------------------------------------------------------------------
# Setup and Mocking
# ---------------------------------------------------------------------------


class DummyApp:
    def __init__(self, root_dir="/tmp"):
        self.config = {"AUTH_MODEL": "MockUser", "SECRET_KEY": "test-secret"}
        self.root_dir = root_dir
        self.models = []


class MockUser(Model):
    _db_path = ":memory:"
    __tablename__ = "mock_users"
    username = Field.String()
    is_admin = Field.Boolean(default=False)
    totp_secret = Field.String(nullable=True)
    totp_enabled = Field.Boolean(default=False)
    backup_codes = Field.String(nullable=True)


# Make sure Role and AdminLog exist in registry to bypass _ensure_model_file
class Role(Model):
    _db_path = ":memory:"
    __tablename__ = "roles"


class AdminLog(Model):
    _db_path = ":memory:"
    __tablename__ = "admin_logs"


# ---------------------------------------------------------------------------
# Impersonation Reversion Tests
# ---------------------------------------------------------------------------


def test_impersonation_reversion_revoked_admin(tmp_path):
    # Setup test DB and register model in MODELS_REGISTRY
    MockUser.create_table()
    Role.create_table()
    AdminLog.create_table()

    # 1. Create an admin who will impersonate
    admin = MockUser.create(username="admin", is_admin=True)
    # 2. Create a target regular user
    target = MockUser.create(username="target", is_admin=False)

    # Initialize Admin instance
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # Build mock request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/dashboard",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    # Set up session with impersonator details
    req._session = Session(
        {
            "user_id": target.id,  # Target's ID
            "impersonator_id": admin.id,  # Impersonating Admin's ID
            "impersonate_started_at": time.time(),
        }
    )
    req.user = None
    req._flashes = []

    def mock_flash(category, message):
        req._flashes.append((category, message))

    req.flash = mock_flash

    # Dispatch should successfully load target as req.user
    try:
        admin_instance.dispatch(req)
    except Exception:
        pass

    assert req.user is not None
    assert req.user.id == target.id

    # 3. Revoke admin's is_admin status
    admin.is_admin = False
    admin.save()

    # Re-run dispatch: impersonator is no longer admin!
    # It should revert session user_id to admin's ID, clear impersonation keys, and flash error
    req.user = None
    try:
        admin_instance.dispatch(req)
    except Exception:
        pass

    assert req.session.get("impersonator_id") is None
    assert req.session.get("impersonate_started_at") is None
    assert req.session.get("user_id") == admin.id
    assert any("Unauthorized impersonation" in f[1] for f in req._flashes)

    MockUser.close_connections()
    Role.close_connections()
    AdminLog.close_connections()


# ---------------------------------------------------------------------------
# File Extension Bypass Tests
# ---------------------------------------------------------------------------


def test_crud_view_blocked_extension_bypass(tmp_path):
    # Test that a blocked file extension (e.g. .php) is rejected and doesn't get saved,
    # even if it has a fake "image/png" content_type.
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # Let's mock a field that has is_file=True and upload_to set
    class MockField:
        is_file = True
        upload_to = str(tmp_path)
        sql_type = "TEXT"

    class MockForm:
        _fields = {}

        def __init__(self):
            pass

    # Create dummy item
    class MockItem:
        pass

    item = MockItem()

    # Create a mock upload file ending in a blocked extension (e.g., .php)
    upload = UploadedFile(
        filename="evil.php",
        content=b"<?php echo 'evil'; ?>",
        content_type="image/png",  # Spoofed MIME type
    )

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/mock/edit",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.files["avatar"] = upload
    req._flashes = []

    def mock_flash(category, message):
        req._flashes.append((category, message))

    req.flash = mock_flash

    admin_instance.t = lambda request, msg, **kwargs: (
        msg.format(**kwargs) if kwargs else msg
    )

    class MockModel:
        __name__ = "MockModel"
        _fields = {"avatar": MockField()}

    entry = {
        "model": MockModel,
        "readonly_fields": [],
        "form_exclude": [],
    }

    form = MockForm()
    admin_instance._apply_form(req, entry, item, form)

    # Verify that it flashed a "File type not allowed" error
    # and did NOT save the file (i.e. upload.filename is not in item, and file is not written)
    assert any("File type not allowed" in f[1] for f in req._flashes)
    assert not hasattr(item, "avatar")
    assert not os.path.exists(os.path.join(str(tmp_path), "evil.php"))


# ---------------------------------------------------------------------------
# Media Manager Error Handling Tests
# ---------------------------------------------------------------------------


def test_media_upload_value_error_handling(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # Mock user who is admin
    class MockAdminUser:
        is_admin = True

    # Mock a file that will raise ValueError on save
    class MockValueErrorFile:
        def __init__(self, filename):
            self.filename = filename

        def save(self, dest, allowed_types=None):
            raise ValueError("Invalid magic bytes or mime-type mismatch")

    # Mock a file that succeeds
    class MockSuccessFile:
        def __init__(self, filename):
            self.filename = filename
            self.saved = False

        def save(self, dest, allowed_types=None):
            self.saved = True

    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/media/upload",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = MockAdminUser()

    # Files
    f_fail = MockValueErrorFile("evil.exe")
    f_ok = MockSuccessFile("avatar.png")
    req.files = {"fail": f_fail, "ok": f_ok}
    req.all_files = [f_fail, f_ok]
    req._flashes = []

    def mock_flash(category, message):
        req._flashes.append((category, message))

    req.flash = mock_flash
    admin_instance.t = lambda request, msg, **kwargs: (
        msg.format(**kwargs) if kwargs else msg
    )

    # Calling _media_upload will raise RedirectException (redirects back to /media)
    with pytest.raises(RedirectException):
        admin_instance._media_upload(req)

    # Check that error flash message was recorded for evil.exe
    assert any("evil.exe: Invalid magic bytes" in f[1] for f in req._flashes)
    # Check that success flash message was recorded for avatar.png
    assert f_ok.saved
    assert any("Successfully uploaded 1 file(s)" in f[1] for f in req._flashes)


# ---------------------------------------------------------------------------
# WSGI Uncaught Exception Tests
# ---------------------------------------------------------------------------


def test_wsgi_uncaught_exception_handling():
    app = Asok()
    app.config["SECRET_KEY"] = "test-secret"
    app.config["DATABASE"] = ":memory:"
    app.config["DEBUG"] = False

    # We mock _dispatch_controller to raise an exception
    def mock_dispatch(req, env):
        raise RuntimeError("Something went wrong inside controller")

    app._dispatch_controller = mock_dispatch

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/somepage",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }

    response_started = []

    def start_response(status, headers):
        response_started.append((status, headers))

    result = app(environ, start_response)
    body = b"".join(result)

    assert response_started[0][0] == "500 Internal Server Error"
    # SECURITY: Error messages should NOT be exposed to clients when DEBUG=False
    # The specific error "Something went wrong" should only be in logs, not in response
    assert b"500" in body or b"internal error" in body.lower()
    assert b"Something went wrong" not in body  # Verify error details are hidden


def test_wsgi_uncaught_exception_handling_debug():
    app = Asok()
    app.config["SECRET_KEY"] = "test-secret"
    app.config["DATABASE"] = ":memory:"
    app.config["DEBUG"] = True

    # We mock _dispatch_controller to raise an exception
    def mock_dispatch(req, env):
        raise RuntimeError("Something went wrong inside controller")

    app._dispatch_controller = mock_dispatch

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/somepage",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }

    response_started = []

    def start_response(status, headers):
        response_started.append((status, headers))

    result = app(environ, start_response)
    body = b"".join(result)

    assert response_started[0][0] == "500 Internal Server Error"
    # In DEBUG=True mode, the traceback and exception message should be visible to the developer
    assert b"RuntimeError" in body
    assert b"Something went wrong inside controller" in body


# ---------------------------------------------------------------------------
# Admin Login without 2FA Test
# ---------------------------------------------------------------------------


def test_admin_login_without_2fa_success(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # 1. Create a user
    MockUser.create_table()
    user = MockUser.create(username="admin", is_admin=True)
    user.email = "admin@example.com"
    user.name = "Admin User"

    # Mock request
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/login",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req._csrf_verified = True
    req.form = {
        "email": "admin@example.com",
        "password": "correct",
    }
    req._flashes = []

    def mock_flash(category, message):
        req._flashes.append((category, message))

    req.flash = mock_flash

    admin_instance.t = lambda request, msg, **kwargs: (
        msg.format(**kwargs) if kwargs else msg
    )

    # Mock request.authenticate and request.login
    # Note: In real code, authenticate() calls login() internally, so we mock authenticate to do both
    login_called = []

    class AuthenticateMock:
        def __call__(self, email, password):
            login_called.append(
                user
            )  # Simulate the login() call that authenticate() does
            return user

    req.authenticate = AuthenticateMock()

    # Mock login too (even though authenticate calls it in real code)
    class LoginMock:
        def __call__(self, u):
            pass  # No-op since authenticate already tracked the call

    req.login = LoginMock()

    # Call _login. It should raise RedirectException because login was successful and it redirects to prefix
    with pytest.raises(RedirectException) as excinfo:
        admin_instance._login(req)

    # Check redirect URL is the admin dashboard prefix
    assert excinfo.value.url == admin_instance.prefix

    # Check request.authenticate was called and recorded the login
    assert len(login_called) == 1
    assert login_called[0].id == user.id

    MockUser.close_connections()


def test_admin_login_csrf_failure(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # 1. Create a user
    MockUser.create_table()
    user = MockUser.create(username="admin", is_admin=True)
    user.email = "admin@example.com"
    user.name = "Admin User"

    # Mock request
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/login",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    # We do NOT set req._csrf_verified = True.
    # We do NOT provide a correct csrf_token in req.form either.
    req.form = {
        "email": "admin@example.com",
        "password": "correct",
    }
    req._flashes = []

    def mock_flash(category, message):
        req._flashes.append((category, message))

    req.flash = mock_flash

    admin_instance.t = lambda request, msg, **kwargs: (
        msg.format(**kwargs) if kwargs else msg
    )
    admin_instance._render = lambda request, template_name, **ctx: (
        f"Rendered {template_name}"
    )

    # Call _login. It should catch SecurityError (from CSRF verification failing)
    # and re-render the login page, flashing the expiration error.
    res = admin_instance._login(req)

    assert res == "Rendered login.html"
    assert any("Security session expired" in f[1] for f in req._flashes)

    MockUser.close_connections()


def test_impersonation_of_non_admin_does_not_redirect_to_login(tmp_path):
    MockUser.create_table()
    Role.create_table()
    AdminLog.create_table()

    # 1. Create an admin who will impersonate
    admin = MockUser.create(username="admin", is_admin=True)
    # 2. Create a target regular user (non-admin)
    target = MockUser.create(username="target", is_admin=False)

    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req._session = Session(
        {
            "user_id": target.id,  # Target's ID
            "impersonator_id": admin.id,  # Impersonating Admin's ID
            "impersonate_started_at": time.time(),
        }
    )
    req.user = None
    req._flashes = []

    # Mock _dashboard and translate
    admin_instance._dashboard = lambda r: "Dashboard"
    admin_instance.t = lambda request, msg, **kwargs: msg

    # Calling dispatch should not raise RedirectException to /login
    # even though target is a non-admin, because the impersonator is a valid admin.
    res = admin_instance.dispatch(req)
    assert res == "Dashboard"
    assert req.user.id == target.id
    assert getattr(req, "impersonator", None) is not None
    assert req.impersonator.id == admin.id

    MockUser.close_connections()
    Role.close_connections()
    AdminLog.close_connections()


def test_user_roles_accessor_and_2fa_update_queries(tmp_path):
    MockUser.create_table()
    Role.create_table()
    MockUser.get_engine().execute("DROP TABLE IF EXISTS role_user")
    MockUser.get_engine().execute(
        "CREATE TABLE role_user (role_id INTEGER, user_id INTEGER)"
    )

    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)  # binds roles property to MockUser

    user = MockUser.create(username="test_user", is_admin=False)
    user.email = "test@example.com"
    user.totp_secret = "encrypted_secret"
    user.totp_enabled = True
    user.backup_codes = '["code1", "code2"]'
    user.save()

    # Verify that calling user.roles executes successfully and returns a ModelList
    roles = user.roles
    assert isinstance(roles, list)
    assert len(roles) == 0

    # Also test the twofa disable/setup updates query execution
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/me/2fa/disable",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = user
    req.form = {"current_password": "correct"}
    req._flashes = []
    req.flash = lambda c, m: None

    # Mock check_password on user
    user.check_password = lambda field, pw: True

    # Call _twofa_disable. It should raise RedirectException because it redirects to /me
    with pytest.raises(RedirectException):
        admin_instance._twofa_disable(req)

    # Let's check that the database values are updated/cleared
    user = MockUser.find(id=user.id)
    assert getattr(user, "totp_secret", None) is None
    assert getattr(user, "totp_enabled", None) in (0, False, None)

    MockUser.close_connections()
    Role.close_connections()


def test_error_template_shared_variables(tmp_path):
    from asok.core import Asok
    from asok.request import Request

    # Create dummy app and register a zero-argument callable
    app = Asok(root_dir=str(tmp_path))
    app.share(some_names=lambda: ["Alice", "Bob"])

    # Create dummy 404 page template that uses the shared variable
    error_dir = tmp_path / "src" / "pages" / "404"
    error_dir.mkdir(parents=True, exist_ok=True)
    error_file = error_dir / "page.html"
    error_file.write_text("Hello page 404: {{ some_names | length }} names")

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/non-existent",
        "asok.app": app,
    }
    req = Request(environ)

    # Set up template root search
    app._tpl_root = [str(tmp_path / "src" / "pages")]

    # Render error page
    html = app._render_error_template(req, str(error_file), "Not Found")
    assert "Hello page 404: 2 names" in html


def test_query_string_hpp():
    from asok.request import Request

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/foo",
        "QUERY_STRING": "next=%2Ffoo%26admin%3D1",
    }
    req = Request(environ)
    assert req.query_string == "next=/foo&admin=1"  # Decoded query string property
    assert "next" in req.args
    assert req.args.get("next") == "/foo&admin=1"
    assert "admin" not in req.args  # Should NOT be parsed as a separate parameter!


def test_csrf_token_cookie_validation():
    from asok.request import Request

    # Malicious cookie with XSS payload
    environ = {
        "REQUEST_METHOD": "POST",
        "HTTP_COOKIE": 'asok_csrf="><script>alert(1)</script>',
    }
    req = Request(environ)
    assert req.csrf_token_value is not None
    assert len(req.csrf_token_value) == 64
    assert req.csrf_token_value != '"><script>alert(1)</script>'

    # Escape validation in csrf_input
    html_input = req.csrf_input()
    assert "<script>" not in html_input


def test_sqlite_memory_no_file(tmp_path):
    import os

    from asok.orm.engines.sqlite import SQLiteEngine

    # Switch to temp directory to avoid affecting actual project root
    old_cwd = os.getcwd()
    os.chdir(str(tmp_path))
    try:
        engine = SQLiteEngine(":memory:")
        conn = engine.get_connection()
        assert conn is not None
        # Verify that no file named ":memory:" was created in the CWD
        assert not os.path.exists(":memory:")
    finally:
        os.chdir(old_cwd)


def test_orm_where_none():
    from asok.orm import Field, Model
    from asok.orm.query import Query

    class DummyModel(Model):
        id = Field.Integer(primary_key=True)
        name = Field.String()

    q1 = Query(DummyModel).where("name", None)
    assert "name IS NULL" in q1._wheres
    assert not q1._args

    q2 = Query(DummyModel).where("name", "=", None)
    assert "name IS NULL" in q2._wheres
    assert not q2._args

    q3 = Query(DummyModel).where("name", "!=", None)
    assert "name IS NOT NULL" in q3._wheres
    assert not q3._args


def test_query_first_and_paginate_cloning():
    from asok.orm import Model
    from asok.orm.query import Query

    class CloningModel(Model):
        pass

    q = Query(CloningModel)
    assert q._limit is None
    assert q._offset is None

    # Mock get and count at class level since first() and paginate() call clone.get() / clone.count()
    original_get = Query.get
    original_count = Query.count
    try:
        Query.get = lambda self: [CloningModel()]
        Query.count = lambda self: 10

        # Call first(), should not set self._limit in place
        q.first()
        assert q._limit is None

        # Call paginate(), should not set self._limit or self._offset in place
        q.paginate(page=2, per_page=5, count=True)
        assert q._limit is None
        assert q._offset is None
    finally:
        Query.get = original_get
        Query.count = original_count


def test_magic_link_token_quoting_and_escaping():
    import os

    from asok.auth import MagicLink
    from asok.request import Request

    environ = {
        "REQUEST_METHOD": "GET",
        "HTTP_HOST": "localhost",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "PATH_INFO": "/foo",
    }
    req = Request(environ)
    # email with plus and special chars
    email = "user+test@example.com"
    os.environ["MAIL_FROM"] = "noreply@example.com"
    try:
        link = MagicLink.send(req, email)
    finally:
        os.environ.pop("MAIL_FROM", None)
    # Verify that the link is properly URL-encoded (so "+" is quoted as "%2B" or "%2b")
    assert "user%2btest" in link.lower()


def test_session_setdefault_modified():
    from asok.session import Session

    s = Session()
    assert not s.modified

    # setdefault on missing key -> should set modified to True
    val = s.setdefault("new_key", "default_val")
    assert val == "default_val"
    assert s.modified

    # Reset modified
    s.modified = False
    # setdefault on existing key -> should not set modified to True
    val = s.setdefault("new_key", "another_val")
    assert val == "default_val"
    assert not s.modified


def test_session_truncation_largest_keys():
    from asok.session import SessionStore

    store = SessionStore()

    # Session with standard key and a huge key
    data = {
        "_user_id": 42,
        "huge_key": "x" * 200_000,
        "another_key": "hello",
    }
    # Perform truncation check
    pruned = store._perform_truncation_check(data)

    # Serialized size should now be under 100KB
    import json

    assert len(json.dumps(pruned)) <= 100_000
    # Core authentication key '_user_id' must be preserved
    assert "_user_id" in pruned
    assert pruned["_user_id"] == 42
    # The huge bloated key should be pruned
    assert "huge_key" not in pruned
    assert "another_key" in pruned


def test_orm_or_where_none():
    from asok.orm import Field, Model
    from asok.orm.query import Query

    class OrWhereDummyModel(Model):
        id = Field.Integer(primary_key=True)
        name = Field.String()

    q = Query(OrWhereDummyModel)
    q.where("name", "val1").or_where("name", None)
    assert "name = ?" in q._wheres
    assert "OR name IS NULL" in q._wheres

    q2 = Query(OrWhereDummyModel)
    q2.where("name", "val1").or_where("name", "!=", None)
    assert "OR name IS NOT NULL" in q2._wheres


def test_safe_next_url_raw_query_string():
    from asok.request import Request

    # Request with parameter injection raw query string
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/login",
        "QUERY_STRING": "next=%2Fdashboard%26admin%3D1",
    }
    req = Request(environ)
    next_url = req._safe_next_url()
    # The raw percent-encoded parameter must be preserved and NOT decoded into a top-level param
    assert "admin%3D1" in next_url
    assert "admin=1" not in next_url


def test_cache_page_key_raw_query_string():
    from asok.cache import cache_page
    from asok.request import Request

    # Two requests with differently encoded query strings
    req1 = Request(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/foo",
            "QUERY_STRING": "q=a%26b",
        }
    )
    req2 = Request(
        {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/foo",
            "QUERY_STRING": "q=a&b",
        }
    )

    # We can mock the cache and function to see the key used
    class MockCache:
        def __init__(self):
            self.keys = []

        def get(self, key):
            self.keys.append(key)
            return None

        def set(self, key, value, ttl=None):
            pass

    cache = MockCache()
    decorator = cache_page(cache_instance=cache)

    @decorator
    def my_view(request):
        return "response"

    my_view(req1)
    my_view(req2)

    # The cache keys should be different!
    assert len(cache.keys) == 2
    assert cache.keys[0] != cache.keys[1]
    assert "q=a%26b" in cache.keys[0]
    assert "q=a&b" in cache.keys[1]
