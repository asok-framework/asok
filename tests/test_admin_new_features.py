from __future__ import annotations

import time

from asok.admin import Admin
from asok.core.asok import Asok
from asok.orm import MODELS_REGISTRY, Field, Model
from asok.session import SessionStore
from asok.testing import TestClient


class Article(Model):
    title = Field.String()
    deleted_at = Field.SoftDelete()


def test_session_store_user_sessions_and_revocation(tmp_path):
    store = SessionStore(backend="memory")
    sid1 = store.generate_sid()
    sid2 = store.generate_sid()
    sid3 = store.generate_sid()

    store.save(
        sid1,
        {
            "user_id": 42,
            "_ip": "127.0.0.1",
            "_user_agent": "Mozilla/5.0",
            "_last_seen": time.time(),
        },
    )
    store.save(
        sid2,
        {
            "user_id": 42,
            "_ip": "10.0.0.1",
            "_user_agent": "Chrome/100",
            "_last_seen": time.time() - 10,
        },
    )
    store.save(
        sid3,
        {
            "user_id": 99,
            "_ip": "192.168.1.1",
            "_user_agent": "Safari/15",
            "_last_seen": time.time(),
        },
    )

    sessions = store.get_user_sessions(42)
    assert len(sessions) == 2
    sids = [s["sid"] for s in sessions]
    assert sid1 in sids
    assert sid2 in sids
    assert sid3 not in sids

    # Revoke sid2 for user 42
    revoked = store.revoke_user_session(42, sid2)
    assert revoked is True
    assert store.load(sid2) is None

    # Try revoking sid1 with wrong user ID
    revoked_wrong = store.revoke_user_session(99, sid1)
    assert revoked_wrong is False
    assert store.load(sid1) is not None


def test_admin_trash_and_restore_views(tmp_path):
    db_file = tmp_path / "test.db"

    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = f"sqlite:///{db_file}"
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    Article._app = app
    app.models = [Article]

    Article.create_table()
    Article.get_engine().execute("DELETE FROM articles")

    Admin(app, site_name="Test Admin")

    User = MODELS_REGISTRY["User"]
    User._app = app
    User.create_table()
    User.get_engine().execute("DELETE FROM users")

    u = User.create(
        _trust=True, email="admin@test.com", password="secretpassword", is_admin=True
    )

    Article.create(_trust=True, title="Active Article")
    art2 = Article.create(_trust=True, title="Soft Deleted Article")
    art2.delete()  # Soft delete art2

    assert Article.query().count() == 1
    assert Article.query().with_trashed().count() == 2
    assert Article.only_trashed().count() == 1

    client = TestClient(app)

    # Set up session for client
    sid = app._session_store.generate_sid()
    app._session_store.save(sid, {"user_id": u.id, "_user_id": u.id})
    client.cookies["asok_sid"] = app._sign(sid)

    # Get Trash view
    res_trash = client.get("/admin/articles/trash")
    assert res_trash.status_code == 200
    assert "Soft Deleted Article" in res_trash.text
    assert "Active Items" in res_trash.text
    assert "restore" in res_trash.text
    assert "force-delete" in res_trash.text

    # Restore art2
    res_restore = client.post(f"/admin/articles/{art2.id}/restore")
    assert res_restore.status_code in (200, 302)
    assert Article.query().count() == 2

    # Delete again & force delete art2
    art2.delete()
    assert Article.only_trashed().count() == 1
    res_force = client.post(f"/admin/articles/{art2.id}/force-delete")
    assert res_force.status_code in (200, 302)
    assert Article.query().with_trashed().count() == 1


def test_admin_me_active_sessions_and_revoke(tmp_path):
    db_file = tmp_path / "test.db"

    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = f"sqlite:///{db_file}"
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    Admin(app, site_name="Test Admin")

    User = MODELS_REGISTRY["User"]
    User._app = app
    User.create_table()
    User.get_engine().execute("DELETE FROM users")

    u = User.create(
        _trust=True, email="admin@test.com", password="secretpassword", is_admin=True
    )

    client = TestClient(app)

    # Main session
    sid1 = app._session_store.generate_sid()
    app._session_store.save(
        sid1,
        {
            "user_id": u.id,
            "_user_id": u.id,
            "_ip": "127.0.0.1",
            "_user_agent": "TestBrowser/1.0",
            "_last_seen": time.time(),
        },
    )
    client.cookies["asok_sid"] = app._sign(sid1)

    # Second active session on another device
    sid2 = app._session_store.generate_sid()
    app._session_store.save(
        sid2,
        {
            "user_id": u.id,
            "_user_id": u.id,
            "_ip": "10.0.0.5",
            "_user_agent": "MobileSafari/15.0",
            "_last_seen": time.time() - 300,
        },
    )

    # Access /admin/me profile page
    res_me = client.get("/admin/me")
    assert res_me.status_code == 200
    assert "Active Sessions" in res_me.text
    assert "MobileSafari/15.0" in res_me.text
    assert "This device" in res_me.text
    assert "Revoke" in res_me.text

    # Revoke sid2 via POST /admin/revoke-session
    res_revoke = client.post("/admin/revoke-session", data={"sid": sid2})
    assert res_revoke.status_code in (200, 302)
    assert app._session_store.load(sid2) is None
