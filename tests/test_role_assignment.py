from __future__ import annotations

from asok.admin import Admin
from asok.core.asok import Asok
from asok.orm import MODELS_REGISTRY
from asok.testing import TestClient


def test_user_role_assignment_discovery_order(tmp_path):
    db_file = tmp_path / "test_roles.db"
    db_url = f"sqlite:///{db_file}"

    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = db_url
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    User = MODELS_REGISTRY["User"]
    Role = MODELS_REGISTRY["Role"]

    User._db_path = db_url
    Role._db_path = db_url
    User._app = app
    Role._app = app
    app.models = [User, Role]

    Admin(app, site_name="Test Admin")

    User.create_table()
    Role.create_table()

    User.get_engine().execute("DELETE FROM users")
    Role.get_engine().execute("DELETE FROM roles")
    User.get_engine().execute("DELETE FROM role_user")

    # Create admin user and moderator role
    admin_user = User.create(
        _trust=True, email="admin@test.com", password="password123", is_admin=True
    )
    mod_role = Role.create(_trust=True, name="moderator", label="Moderator")
    target_user = User.create(
        _trust=True, email="target@test.com", password="password123", is_admin=False
    )

    client = TestClient(app)

    # Log in as admin_user
    sid = app._session_store.generate_sid()
    app._session_store.save(sid, {"user_id": admin_user.id, "_user_id": admin_user.id})
    client.cookies["asok_sid"] = app._sign(sid)

    # Assign moderator role to target_user
    res = client.post(
        f"/admin/users/{target_user.id}",
        data={
            "email": "target@test.com",
            "m2m_roles": str(mod_role.id),
        },
    )

    assert res.status_code in (200, 302)

    # Reload target_user from DB and check roles
    updated_user = User.find(id=target_user.id)
    role_names = [r.name for r in updated_user.roles]
    assert "moderator" in role_names
