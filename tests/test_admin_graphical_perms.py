"""Graphical row/field access rules, configured in /admin/roles — no code in models.

A role can carry two self-describing permission strings, produced by the admin's
permission-matrix checkboxes (see asok/admin/access_rules.py):

    <slug>.own           -> only sees / edits records it owns (owner FK == user.id)
    <slug>.lock:<field>  -> that field is read-only (hidden) for the role

The owner column (a FK pointing at User, e.g. user_id) is auto-detected, and an
own-scoped creator gets it stamped automatically — so a writer never sees or
picks another user. Superusers / roles holding "*" are never restricted.
"""

import io
import types

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.admin.access_rules import (
    resolve_owner_field,
    user_is_own_scoped,
    user_locked_fields,
)
from asok.admin.forms import _build_perm_refine
from asok.orm import MODELS_REGISTRY
from asok.request import Request


class GpPost(Model):
    __tablename__ = "gp_posts"
    title = Field.String()
    status = Field.String(default="pending")
    user_id = Field.ForeignKey("User")


class GpNoOwner(Model):
    __tablename__ = "gp_noowner"
    title = Field.String()


class GpExplicit(Model):
    __tablename__ = "gp_explicit"
    user_id = Field.ForeignKey("User")
    editor_id = Field.ForeignKey("User")

    class Admin:
        owner_field = "editor_id"


def _role(perms):
    return types.SimpleNamespace(permissions=perms)


def _user(perms=None, is_admin=False, uid=1):
    roles = [_role(perms)] if perms is not None else []
    return types.SimpleNamespace(
        id=uid, is_admin=is_admin, roles=roles, can=lambda p: False
    )


# ── owner-field detection (unit) ────────────────────────────────────


def test_owner_field_detects_user_fk():
    assert resolve_owner_field(GpPost) == "user_id"


def test_owner_field_none_without_user_fk():
    assert resolve_owner_field(GpNoOwner) is None


def test_explicit_owner_field_wins():
    # Two User FKs -> would default to user_id, but Admin.owner_field overrides.
    assert resolve_owner_field(GpExplicit) == "editor_id"


# ── own-scope resolution (unit) ─────────────────────────────────────


def test_own_scope_true_for_own_role():
    assert user_is_own_scoped(_user("posts.view,posts.own"), "posts") is True


def test_own_scope_false_without_own():
    assert user_is_own_scoped(_user("posts.view,posts.edit"), "posts") is False


def test_own_scope_false_for_superadmin():
    assert user_is_own_scoped(_user("posts.own", is_admin=True), "posts") is False


def test_own_scope_false_for_wildcard_role():
    assert user_is_own_scoped(_user("*"), "posts") is False


def test_own_scope_not_implied_by_verb_wildcard():
    # posts.* grants every verb but must NOT silently add the "own" restriction.
    assert user_is_own_scoped(_user("posts.*"), "posts") is False


# ── locked-field resolution (unit) ──────────────────────────────────


def test_locked_fields_collects_locks():
    assert user_locked_fields(_user("posts.edit,posts.lock:status"), "posts") == {
        "status"
    }


def test_locked_fields_scoped_per_slug():
    u = _user("posts.lock:status,comments.lock:body")
    assert user_locked_fields(u, "posts") == {"status"}
    assert user_locked_fields(u, "comments") == {"body"}


def test_locked_fields_empty_for_superadmin():
    assert (
        user_locked_fields(_user("posts.lock:status", is_admin=True), "posts") == set()
    )


def test_locked_fields_empty_for_wildcard():
    assert user_locked_fields(_user("*"), "posts") == set()


# ── matrix refinement payload (unit) ────────────────────────────────


def test_perm_refine_payload():
    slug = "gp_posts"
    refine = _build_perm_refine(slug, GpPost, {f"{slug}.own", f"{slug}.lock:status"})
    assert refine["owner_field"] == "user_id"
    assert refine["own_checked"] is True
    assert refine["has_refine"] is True
    checked = {lf["name"]: lf["checked"] for lf in refine["lock_fields"]}
    assert checked["status"] is True
    assert checked["title"] is False


def test_perm_refine_absent_without_owner_or_fields():
    refine = _build_perm_refine("gp_noowner", GpNoOwner, set())
    assert refine["owner_field"] is None
    # "title" is still lockable, so the row can be refined
    assert refine["has_refine"] is True


# ── integration with the admin CRUD flow ────────────────────────────


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "AUTH_MODEL": "User"})
    MODELS_REGISTRY["GpPost"] = GpPost
    GpPost.get_engine().execute("DROP TABLE IF EXISTS gp_posts")
    GpPost.create_table()
    a = Admin(app)
    a.register(GpPost)
    return a


def _entry(admin):
    return next(e for e in admin._registered.values() if e["model"] is GpPost)


def _slug(admin):
    return _entry(admin)["slug"]


def _req(app, actor, body=""):
    raw = body.encode()
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/a",
        "QUERY_STRING": "",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": str(len(raw)),
        "wsgi.input": io.BytesIO(raw),
        "asok.app": app,
    }
    r = Request(env)
    r._user_instance = actor
    r._auth_resolved = True
    return r


def test_writer_form_hides_owner_and_locked_fields(admin):
    slug = _slug(admin)
    writer = _user(f"{slug}.view,{slug}.add,{slug}.own,{slug}.lock:status")
    art = GpPost(title="A", status="pending", user_id=1)
    art.save()
    form, _meta = admin._build_form(_req(admin.app, writer), _entry(admin), art)
    assert "status" not in form._fields  # locked -> hidden
    assert "user_id" not in form._fields  # owner auto-managed -> hidden
    assert "title" in form._fields


def test_unrestricted_user_sees_everything(admin):
    slug = _slug(admin)
    editor = _user(f"{slug}.view,{slug}.add,{slug}.edit")
    art = GpPost(title="A", status="pending", user_id=1)
    art.save()
    form, _meta = admin._build_form(_req(admin.app, editor), _entry(admin), art)
    assert "status" in form._fields
    assert "user_id" in form._fields


def test_owner_stamped_on_create_for_own_scoped(admin):
    slug = _slug(admin)
    writer = _user(f"{slug}.add,{slug}.own", uid=42)
    item = GpPost()
    admin._apply_owner_default(_req(admin.app, writer), _entry(admin), item)
    assert item.user_id == 42


def test_owner_not_stamped_for_unscoped_user(admin):
    slug = _slug(admin)
    normal = _user(f"{slug}.add", uid=42)
    item = GpPost()
    admin._apply_owner_default(_req(admin.app, normal), _entry(admin), item)
    assert getattr(item, "user_id", None) in (None, 0)


def test_own_scope_filters_list_query(admin):
    slug = _slug(admin)
    GpPost(title="mine", status="p", user_id=7).save()
    GpPost(title="theirs", status="p", user_id=9).save()
    writer = _user(f"{slug}.view,{slug}.own", uid=7)
    q = admin._apply_admin_scope(_req(admin.app, writer), GpPost.query(), _entry(admin))
    assert {r.title for r in q.get()} == {"mine"}


def test_own_scope_filters_bulk_ids(admin):
    # SECURITY regression: bulk actions must honour the graphical `.own` rule even
    # when the model has no code `scope()` callback. Otherwise an own-scoped user
    # could delete/edit other users' rows by POSTing arbitrary ids to /bulk.
    mine = GpPost(title="mine", status="p", user_id=7)
    mine.save()
    theirs = GpPost(title="theirs", status="p", user_id=9)
    theirs.save()
    slug = _slug(admin)
    writer = _user(f"{slug}.delete,{slug}.own", uid=7)
    filtered = admin._scope_bulk_ids(
        _req(admin.app, writer), _entry(admin), [mine.id, theirs.id]
    )
    assert filtered == [mine.id]  # theirs.id dropped


def test_bulk_ids_unrestricted_for_admin(admin):
    # A superadmin (no code scope, not own-scoped) keeps every id.
    a1 = GpPost(title="a", status="p", user_id=7)
    a1.save()
    a2 = GpPost(title="b", status="p", user_id=9)
    a2.save()
    superadmin = _user("*", is_admin=True, uid=1)
    filtered = admin._scope_bulk_ids(
        _req(admin.app, superadmin), _entry(admin), [a1.id, a2.id]
    )
    assert set(filtered) == {a1.id, a2.id}


# ── dashboard: stats scoped, activity charts gated ──────────────────


def test_dashboard_stat_count_is_owner_scoped(admin):
    # An own-scoped user's dashboard card must count THEIR rows, not the global
    # total — otherwise the card leaks the aggregate volume of others' data.
    GpPost(title="mine", status="p", user_id=7).save()
    GpPost(title="mine2", status="p", user_id=7).save()
    GpPost(title="theirs", status="p", user_id=9).save()
    slug = _slug(admin)
    entry = _entry(admin)
    writer = _user(f"{slug}.view,{slug}.own", uid=7)
    stat = admin._build_dashboard_stat(_req(admin.app, writer), slug, entry)
    assert stat["count"] == 2  # only user 7's rows, not the 3 total


def test_dashboard_stat_count_global_for_admin(admin):
    GpPost(title="a", status="p", user_id=7).save()
    GpPost(title="b", status="p", user_id=9).save()
    slug = _slug(admin)
    entry = _entry(admin)
    superadmin = _user("*", is_admin=True, uid=1)
    stat = admin._build_dashboard_stat(_req(admin.app, superadmin), slug, entry)
    assert stat["count"] == 2  # admin sees the full total


def test_activity_charts_gated_by_log_view(admin):
    # The activity timelines are derived from AdminLog. Without logs.view they
    # must be zeroed (no leak of global admin-activity volume), while the model
    # chart built from the permission-filtered stats still renders — so the gate
    # is specific to log data, not a blanket "empty dashboard".
    stats = [{"slug": "gp_posts", "label": "Posts", "count": 5, "trend": None}]
    chart = admin._collect_dashboard_chart_data(stats, can_view_logs=False)

    assert 5 in chart["model_data"]  # non-log model chart still populated
    assert sum(chart["timeline_data"]) == 0
    assert sum(chart["monthly_data"]) == 0
    assert sum(chart["yearly_data"]) == 0
    # labels still present so the chart renders as an empty axis
    assert len(chart["timeline_labels"]) == 7


# ── role permissions writable only by super-admin ───────────────────


def _perm_entry():
    return {"readonly_fields": [], "form_exclude": [], "slug": "roles", "model": None}


def _perm_field():
    return types.SimpleNamespace(
        is_password=False, protected=False, is_timestamp=False, is_file=False
    )


def test_permissions_not_writable_by_non_admin(admin):
    # A `roles.edit` delegate may manage a role's other fields, but must NOT be
    # able to (re)write its permission set — that would be equivalent to minting
    # super-admin. The stored value is preserved, not applied/wiped.
    item = types.SimpleNamespace(permissions="posts.view")
    req = _req(admin.app, _user("roles.edit", uid=5), body="permissions=*")
    handled = admin._handle_special_apply_fields(
        req, _perm_entry(), "permissions", _perm_field(), item, True, False
    )
    assert handled is True
    assert item.permissions == "posts.view"  # unchanged — the "*" grant ignored


def test_permissions_writable_by_admin(admin):
    item = types.SimpleNamespace(permissions="posts.view")
    req = _req(admin.app, _user("*", is_admin=True, uid=1), body="permissions=*")
    admin._handle_special_apply_fields(
        req, _perm_entry(), "permissions", _perm_field(), item, True, False
    )
    assert item.permissions == "*"  # super-admin can define permissions


# ── i18n: boolean column values follow the active locale ────────────


def test_boolean_column_value_is_translated(admin):
    # Regression: list/detail boolean cells hard-coded English "Yes"/"No".
    # dispatch() now sets self.locale so render helpers can translate.
    field = types.SimpleNamespace(sql_type="INTEGER")
    admin.locale = "fr"
    assert "Oui" in str(admin._col_value_boolean(field, "is_active", 1))
    assert "Non" in str(admin._col_value_boolean(field, "is_active", 0))
    admin.locale = "en"
    assert "Yes" in str(admin._col_value_boolean(field, "is_active", 1))


def test_filter_options_translated(admin):
    admin.locale = "es"
    field = types.SimpleNamespace(sql_type="INTEGER")
    opts = admin._build_filter_options(field, "is_active", [1, 0], "")
    labels = [o["label"] for o in opts]
    assert "Todo" in labels and "Sí" in labels and "No" in labels


class GpDrift(Model):
    __tablename__ = "gp_drift"
    title = Field.String()
    author_id = Field.ForeignKey("User")  # declared in model, absent from table below


def test_own_scope_fails_closed_on_missing_owner_column():
    # Reproduces model/DB drift: the model declares author_id but the physical
    # table has no such column. Own-scope must NOT crash the list (no 500) and
    # must NOT leak rows — it returns nothing until the schema is migrated.
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "AUTH_MODEL": "User"})
    MODELS_REGISTRY["GpDrift"] = GpDrift
    eng = GpDrift.get_engine()
    eng.execute("DROP TABLE IF EXISTS gp_drift")
    eng.execute(
        "CREATE TABLE gp_drift (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT)"
    )
    eng.execute("INSERT INTO gp_drift (title) VALUES ('a'), ('b')")
    a = Admin(app)
    a.register(GpDrift)
    entry = next(e for e in a._registered.values() if e["model"] is GpDrift)

    assert a._table_has_column(GpDrift, "author_id") is False
    writer = _user(f"{entry['slug']}.view,{entry['slug']}.own", uid=1)
    q = a._apply_admin_scope(_req(a.app, writer), GpDrift.query(), entry)
    assert q.count() == 0  # fail closed, no crash


def test_superadmin_scope_sees_all_rows(admin):
    slug = _slug(admin)
    GpPost(title="mine", status="p", user_id=7).save()
    GpPost(title="theirs", status="p", user_id=9).save()
    boss = _user(f"{slug}.own", is_admin=True, uid=7)
    q = admin._apply_admin_scope(_req(admin.app, boss), GpPost.query(), _entry(admin))
    assert {r.title for r in q.get()} == {"mine", "theirs"}


def test_trash_forbidden_for_non_admin(admin):
    # Trash (bin) is admin-only server-side, not just hidden in the UI.
    writer = _user(f"{_slug(admin)}.view")
    req = _req(admin.app, writer)
    admin._trash(req, _entry(admin))
    assert req.status_code() == 403


def test_trash_guard_passes_for_admin(admin):
    boss = _user(f"{_slug(admin)}.view", is_admin=True)
    req = _req(admin.app, boss)
    admin._trash(req, _entry(admin))
    # Guard passed -> GpPost has no soft-delete, so it falls through to 404.
    assert req.status_code() == 404


def test_locked_field_ignored_on_apply(admin):
    slug = _slug(admin)
    # Build the form as an unrestricted editor so `status` captures the posted
    # value, then apply as a locked writer -> the value must be refused.
    editor = _user(f"{slug}.edit")
    writer = _user(f"{slug}.edit,{slug}.lock:status")
    art = GpPost(title="A", status="pending", user_id=1)
    art.save()
    form, _m = admin._build_form(
        _req(admin.app, editor, "title=A&status=published"), _entry(admin), art
    )
    admin._apply_form(_req(admin.app, writer), _entry(admin), art, form)
    assert art.status == "pending"  # locked field preserved server-side
