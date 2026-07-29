from __future__ import annotations

import time

from asok.admin import Admin
from asok.core.asok import Asok
from asok.orm import MODELS_REGISTRY
from asok.orm.engines import _ENGINES_CACHE
from asok.session import SessionStore
from asok.testing import TestClient


def test_session_store_get_all_active_sessions(tmp_path):
    store = SessionStore(backend="file", path=str(tmp_path / "sessions"), ttl=3600)

    sid1 = store.generate_sid()
    sid2 = store.generate_sid()
    sid3 = store.generate_sid()

    store.save(sid1, {"user_id": 10, "_ip": "127.0.0.1", "_user_agent": "Mozilla/5.0 (Macintosh)", "_last_seen": time.time()})
    store.save(sid2, {"user_id": 10, "_ip": "192.168.1.1", "_user_agent": "Mozilla/5.0 (iPhone)", "_last_seen": time.time() - 100})
    store.save(sid3, {"user_id": 20, "_ip": "10.0.0.1", "_user_agent": "Mozilla/5.0 (Windows)", "_last_seen": time.time() - 2000})

    active = store.get_all_active_sessions()
    assert len(active) == 3

    user10_sess = store.get_user_sessions(10)
    assert len(user10_sess) == 2

    # Revoke all for user 10
    revoked_count = store.revoke_all_user_sessions(10)
    assert revoked_count == 2
    assert len(store.get_user_sessions(10)) == 0
    assert len(store.get_all_active_sessions()) == 1


def test_admin_active_sessions_view_and_permissions(tmp_path):
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"

    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = db_url
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    Admin(app, site_name="Test Admin")

    User = MODELS_REGISTRY["User"]
    Role = MODELS_REGISTRY["Role"]

    User._db_path = db_url
    Role._db_path = db_url
    User._app = app
    Role._app = app
    app.models = [User, Role]

    try:
        User.create_table()
        Role.create_table()

        User.get_engine().execute("DELETE FROM users")
        Role.get_engine().execute("DELETE FROM roles")

        admin_user = User.create(_trust=True, email="admin@test.com", password="password123", is_admin=True)
        normal_user = User.create(_trust=True, email="user@test.com", password="password123", is_admin=False)

        client = TestClient(app)

        # 1. Non-admin access should be redirected / forbidden
        sid_user = app._session_store.generate_sid()
        app._session_store.save(sid_user, {"user_id": normal_user.id, "_user_id": normal_user.id, "_ip": "127.0.0.1", "_user_agent": "Desktop"})
        client.cookies["asok_sid"] = app._sign(sid_user)

        res = client.get("/admin/active-sessions")
        assert res.status_code in (302, 403)

        # 2. Admin access should succeed (200)
        sid_admin = app._session_store.generate_sid()
        app._session_store.save(sid_admin, {"user_id": admin_user.id, "_user_id": admin_user.id, "_ip": "127.0.0.1", "_user_agent": "Macintosh"})
        client.cookies["asok_sid"] = app._sign(sid_admin)

        res = client.get("/admin/active-sessions")
        assert res.status_code == 200
        assert "Active Admin Sessions" in res.text

        # 3. Test Revoke single session
        res_revoke = client.post("/admin/active-sessions/revoke", data={"sid": sid_user, "filter": "all"})
        assert res_revoke.status_code in (200, 302)
        assert app._session_store.load(sid_user) is None

        # 4. Test Revoke all user sessions (Force Logout)
        sid_user2 = app._session_store.generate_sid()
        app._session_store.save(sid_user2, {"user_id": normal_user.id, "_user_id": normal_user.id})
        res_revoke_all = client.post("/admin/active-sessions/revoke-user", data={"user_id": str(normal_user.id), "filter": "all"})
        assert res_revoke_all.status_code in (200, 302)
        assert app._session_store.load(sid_user2) is None
    finally:
        User._db_path = None
        Role._db_path = None
        User._app = None
        Role._app = None
        _ENGINES_CACHE.clear()
