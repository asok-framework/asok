"""M2M sync/attach/detach auto-provision a missing pivot table.

Regression: pivot tables are created by `create_table()`, but a schema built
purely through migrations may lack them — so `post.sync("tags", ids)` raised
`sqlite3.OperationalError: no such table: post_tag`. attach/detach/sync now run
`CREATE TABLE IF NOT EXISTS` for the pivot before touching it.
"""

import pytest

from asok import Asok, Field, Model, Relation
from asok.orm import MODELS_REGISTRY


class PvTag(Model):
    _db_path = ":memory:"
    __tablename__ = "pv_tags"
    name = Field.String()


class PvPost(Model):
    _db_path = ":memory:"
    __tablename__ = "pv_posts"
    title = Field.String()
    tags = Relation.BelongsToMany("PvTag", pivot_table="pv_post_tag")


def _pivot_missing() -> bool:
    rows = PvPost.get_engine().execute("PRAGMA table_info(pv_post_tag)")
    return len(list(rows)) == 0


@pytest.fixture(autouse=True)
def _db():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["PvTag"] = PvTag
    MODELS_REGISTRY["PvPost"] = PvPost
    PvTag.create_table()
    PvPost.create_table()  # this also creates the pivot...
    PvPost.get_engine().execute(
        "DROP TABLE IF EXISTS pv_post_tag"
    )  # ...simulate a migration-only setup that never made the pivot
    return app


def test_sync_autocreates_missing_pivot():
    assert _pivot_missing()  # precondition: the exact failure state
    p = PvPost.create(_trust=True, title="P")
    t1 = PvTag.create(_trust=True, name="a")
    t2 = PvTag.create(_trust=True, name="b")
    p.sync("tags", [t1.id, t2.id])  # used to raise: no such table: pv_post_tag
    assert {tag.id for tag in p.tags} == {t1.id, t2.id}


def test_attach_autocreates_missing_pivot():
    assert _pivot_missing()
    p = PvPost.create(_trust=True, title="P2")
    t = PvTag.create(_trust=True, name="c")
    p.attach("tags", [t.id])
    assert {tag.id for tag in p.tags} == {t.id}


def test_detach_autocreates_missing_pivot():
    assert _pivot_missing()
    p = PvPost.create(_trust=True, title="P3")
    t = PvTag.create(_trust=True, name="d")
    p.detach("tags", [t.id])  # nothing to remove, but must not raise on missing pivot
    assert list(p.tags) == []


def test_admin_m2m_selected_ids_reflect_current_relation():
    # Regression: relations are properties (ModelList), not methods. The admin
    # helper called `getattr(item, rel)()` → TypeError swallowed → no ids marked
    # selected, so existing m2m (roles, tags…) were never pre-checked in the edit
    # form and a save wiped them ("you can't remove all your roles").
    from asok.admin.forms import _fetch_selected_ids

    p = PvPost.create(_trust=True, title="P4")
    t1 = PvTag.create(_trust=True, name="x")
    t2 = PvTag.create(_trust=True, name="y")
    p.sync("tags", [t1.id, t2.id])

    assert set(_fetch_selected_ids(p, "tags")) == {t1.id, t2.id}


def test_admin_m2m_option_label_uses_display_not_model_id():
    # Regression: m2m options were labelled "<Model> #<id>" instead of the
    # object's display name (Role #1 → the role's name).
    from asok.admin.forms import _fetch_all_target_options

    PvTag.create(_trust=True, name="Python")
    labels = {o["label"] for o in _fetch_all_target_options(PvTag)}
    assert "Python" in labels
    assert not any(lbl.startswith("PvTag #") for lbl in labels)
