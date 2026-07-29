"""Regression tests for admin CRUD field-level authorization.

SECURITY: a user with `users.edit`/`users.add` permission but WITHOUT is_admin
must not be able to set the privilege field `is_admin` by POSTing it. The
privilege-field stripping (_strip_secured_fields) used to run only on the render
path, so the POST handler (_apply_form) applied is_admin straight from the form
=> privilege escalation. These tests lock the write-path behaviour.
"""

import io

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.request import Request


class User(Model):
    _table = "users"
    email = Field.String()
    password = Field.Password()
    is_admin = Field.Boolean(default=False)


class Role(Model):
    _table = "roles"
    name = Field.String()
    label = Field.String()
    permissions = Field.Text()


@pytest.fixture
def app():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "AUTH_MODEL": "User"})

    from asok.orm import MODELS_REGISTRY

    MODELS_REGISTRY["User"] = User
    MODELS_REGISTRY["Role"] = Role

    engine = User.get_engine()
    engine.execute("PRAGMA foreign_keys = OFF")
    for tbl in ("role_user", "users", "roles"):
        engine.execute(f"DROP TABLE IF EXISTS {tbl}")
    engine.execute("PRAGMA foreign_keys = ON")
    User.create_table()
    Role.create_table()
    engine.execute(
        "CREATE TABLE IF NOT EXISTS role_user (user_id INTEGER, role_id INTEGER)"
    )

    admin = Admin(app)
    admin.register(User)
    admin.register(Role)
    return app, admin


def _entry_for(admin, model):
    return next(e for e in admin._registered.values() if e["model"] is model)


def _post_request(app, actor, body: str) -> Request:
    raw = body.encode()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/users/edit",
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "asok.app": app,
    }
    request = Request(environ)
    # Inject the authenticated actor directly (mirrors Request.login()).
    request._user_instance = actor
    request._auth_resolved = True
    return request


def test_non_admin_cannot_set_is_admin_on_other_user(app):
    """A non-admin editor POSTing is_admin=1 on another account must be ignored."""
    app_inst, admin = app
    entry = _entry_for(admin, User)

    attacker = User(email="attacker@x.com", password="pw", is_admin=False)
    attacker.save()
    target = User(email="victim@x.com", password="pw", is_admin=False)
    target.save()

    request = _post_request(app_inst, attacker, "email=victim%40x.com&is_admin=1")
    form, _meta = admin._build_form(request, entry, target)
    admin._apply_form(request, entry, target, form)

    assert not target.is_admin, "privilege escalation: is_admin was applied from POST"


def test_non_admin_edit_does_not_demote_existing_admin(app):
    """Editing an admin as a non-admin must PRESERVE is_admin (no accidental zeroing)."""
    app_inst, admin = app
    entry = _entry_for(admin, User)

    attacker = User(email="attacker@x.com", password="pw", is_admin=False)
    attacker.save()
    admin_target = User(email="boss@x.com", password="pw", is_admin=True)
    admin_target.save()

    # is_admin omitted from the body (as a real stripped form would submit).
    request = _post_request(app_inst, attacker, "email=boss%40x.com")
    form, _meta = admin._build_form(request, entry, admin_target)
    admin._apply_form(request, entry, admin_target, form)

    assert admin_target.is_admin, "integrity bug: existing admin was demoted on edit"


def test_super_admin_can_still_set_is_admin(app):
    """A real super-admin editing another account keeps the ability to grant is_admin."""
    app_inst, admin = app
    entry = _entry_for(admin, User)

    superadmin = User(email="root@x.com", password="pw", is_admin=True)
    superadmin.save()
    target = User(email="promote@x.com", password="pw", is_admin=False)
    target.save()

    request = _post_request(app_inst, superadmin, "email=promote%40x.com&is_admin=1")
    form, _meta = admin._build_form(request, entry, target)
    admin._apply_form(request, entry, target, form)

    assert target.is_admin, "regression: super-admin can no longer grant is_admin"


def test_non_admin_cannot_self_escalate_is_admin(app):
    """A non-admin editing their own account cannot flip their own is_admin."""
    app_inst, admin = app
    entry = _entry_for(admin, User)

    me = User(email="me@x.com", password="pw", is_admin=False)
    me.save()

    request = _post_request(app_inst, me, "email=me%40x.com&is_admin=1")
    form, _meta = admin._build_form(request, entry, me)
    admin._apply_form(request, entry, me, form)

    assert not me.is_admin, "self privilege escalation via is_admin"
