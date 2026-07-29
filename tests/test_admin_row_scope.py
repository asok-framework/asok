"""Row-level admin scoping: `class Admin: scope = staticmethod(fn)` (or
admin.register(Model, scope=fn)) narrows what a user can see/touch.

fn(request, query) -> query. Applied to the list, single-item lookup (so a user
can't open another row by URL — IDOR), and bulk actions (ids the scope hides are
dropped). Used e.g. so a Writer only sees the articles they authored.
"""

import io
import types

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.orm import MODELS_REGISTRY
from asok.request import Request


class Post(Model):
    __tablename__ = "posts_scope"
    title = Field.String()
    author_id = Field.Integer()


def _own_posts_scope(request, query):
    u = request.user
    if u and getattr(u, "is_admin", False):
        return query
    return query.where("author_id", getattr(u, "id", 0))


@pytest.fixture
def ctx():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "AUTH_MODEL": "User", "CSRF": False})
    MODELS_REGISTRY["Post"] = Post
    Post.get_engine().execute("DROP TABLE IF EXISTS posts_scope")
    Post.create_table()
    admin = Admin(app)
    admin.register(Post, scope=_own_posts_scope)
    p1 = Post.create(_trust=True, title="w1-a", author_id=1)
    p2 = Post.create(_trust=True, title="w2-a", author_id=2)
    p3 = Post.create(_trust=True, title="w1-b", author_id=1)
    entry = next(e for e in admin._registered.values() if e["model"] is Post)
    return admin, entry, (p1, p2, p3)


def _writer(uid):
    return types.SimpleNamespace(id=uid, is_admin=False, can=lambda p: False)


def _superadmin():
    return types.SimpleNamespace(id=99, is_admin=True, can=lambda p: True)


def _get(app, actor, qs=""):
    env = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/posts",
        "QUERY_STRING": qs,
        "asok.app": app,
    }
    r = Request(env)
    r._user_instance = actor
    r._auth_resolved = True
    return r


def _post(app, actor, body):
    raw = body.encode()
    env = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/admin/posts/bulk",
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


def test_list_is_scoped_to_own_rows(ctx):
    admin, entry, _ = ctx
    titles = {p.title for p in admin._build_query(_get(admin.app, _writer(1)), entry).get()}
    assert titles == {"w1-a", "w1-b"}  # only author_id == 1


def test_admin_sees_all_rows(ctx):
    admin, entry, _ = ctx
    titles = {p.title for p in admin._build_query(_get(admin.app, _superadmin()), entry).get()}
    assert titles == {"w1-a", "w2-a", "w1-b"}


def test_single_item_lookup_blocks_foreign_row(ctx):
    admin, entry, (p1, p2, _p3) = ctx
    # writer 1 can open their own post
    assert admin._lookup_admin_item(_get(admin.app, _writer(1)), entry, p1.id) is not None
    # ...but NOT writer 2's post (IDOR via /admin/posts/<id>/edit)
    assert admin._lookup_admin_item(_get(admin.app, _writer(1)), entry, p2.id) is None


def test_bulk_ids_are_filtered_to_scope(ctx):
    admin, entry, (p1, p2, p3) = ctx
    req = _post(admin.app, _writer(1), f"ids={p1.id},{p2.id},{p3.id}")
    ids = admin._parse_bulk_ids(req, entry)
    assert set(ids) == {p1.id, p3.id}  # p2 (writer 2) dropped


def test_no_scope_is_unrestricted(ctx):
    admin, _entry, _ = ctx
    # a model registered without a scope behaves as before
    admin.register(Post, scope=None)
    entry = next(e for e in admin._registered.values() if e["model"] is Post)
    titles = {p.title for p in admin._build_query(_get(admin.app, _writer(1)), entry).get()}
    assert titles == {"w1-a", "w2-a", "w1-b"}
