"""Tests for RBAC improvements: permission logging and role self-protection."""

from unittest.mock import MagicMock, patch

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.testing import TestClient


class User(Model):
    email = Field.String()
    password = Field.Password()
    is_admin = Field.Boolean(default=False)


class Role(Model):
    name = Field.String()
    label = Field.String()
    permissions = Field.Text()


class Post(Model):
    title = Field.String()
    content = Field.Text()


@pytest.fixture
def app():
    """Create test app with Admin."""
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "AUTH_MODEL": "User"})

    # Ensure correct models are in the registry after Asok() setup has run
    from asok.orm import MODELS_REGISTRY
    MODELS_REGISTRY["User"] = User
    MODELS_REGISTRY["Role"] = Role

    # Create tables
    User.create_table()
    Role.create_table()
    Post.create_table()

    # Create admin instance
    admin = Admin(app)

    return app, admin


@pytest.fixture
def client(app):
    """Create test client."""
    app_instance, _ = app
    return TestClient(app_instance)


class TestPermissionLogging:
    """Test that permission denials are logged for audit trail."""

    def test_permission_denial_logged_no_user(self, app, client):
        """Test that permission checks are logged at DEBUG level (not WARNING to avoid noise)."""
        _, admin = app

        with patch("asok.admin.rbac.logger") as mock_logger:
            # Create a mock request with no user
            request = MagicMock()
            request.user = None

            # Check permission
            result = admin._can(request, "posts", "edit")

            # Verify denied
            assert result is False

            # Verify logged at DEBUG level (routine checks shouldn't spam WARNING logs)
            mock_logger.debug.assert_called_once()
            call_args = mock_logger.debug.call_args[0][0]
            assert "Permission check" in call_args
            assert "No authenticated user" in call_args
            assert "posts" in call_args
            assert "edit" in call_args

    def test_permission_denial_logged_insufficient_perms(self, app, client):
        """Test that permission checks are logged at DEBUG level when user lacks permission."""
        _, admin = app

        with patch("asok.admin.rbac.logger") as mock_logger:
            # Create a mock user with no admin rights
            user = MagicMock()
            user.id = 1
            user.email = "test@example.com"
            user.is_admin = False

            # Mock the can method to return False
            user.can = MagicMock(return_value=False)

            # Create mock request
            request = MagicMock()
            request.user = user

            # Check permission
            result = admin._can(request, "posts", "delete")

            # Verify denied
            assert result is False

            # Verify logged at DEBUG level (routine UI checks shouldn't spam logs)
            mock_logger.debug.assert_called()
            call_args = mock_logger.debug.call_args[0][0]
            assert "Permission check" in call_args
            assert "test@example.com" in call_args
            assert "posts" in call_args
            assert "delete" in call_args
            assert "no permission" in call_args

    def test_permission_granted_no_log(self, app, client):
        """Test that successful permission checks don't log anything."""
        _, admin = app

        with patch("asok.admin.rbac.logger") as mock_logger:
            # Create a mock admin user
            user = MagicMock()
            user.id = 1
            user.email = "admin@example.com"
            user.is_admin = True

            # Create mock request
            request = MagicMock()
            request.user = user

            # Check permission (admin bypasses checks)
            result = admin._can(request, "posts", "delete")

            # Verify granted
            assert result is True

            # Verify NOT logged (no debug/warning for successful checks)
            mock_logger.debug.assert_not_called()
            mock_logger.warning.assert_not_called()


class TestRoleSelfProtection:
    """Test that users cannot remove all their roles."""

    def test_user_can_change_roles_with_at_least_one(self, app, client):
        """Test that users can change their roles as long as one remains."""
        _, admin = app

        # Create user and roles
        user = User(email="test@example.com", password="password123", is_admin=True)
        user.save()

        role1 = Role(name="editor", label="Editor", permissions="posts.view,posts.edit")
        role1.save()

        role2 = Role(name="viewer", label="Viewer", permissions="posts.view")
        role2.save()

        # Assign both roles
        user.sync("roles", [role1.id, role2.id])

        # Mock request simulating user removing role1 but keeping role2
        request = MagicMock()
        request.user = user
        request.form = {"m2m_roles": str(role2.id)}  # Only keep role2
        request.flash = MagicMock()

        # Simulate sync
        admin._sync_m2m(request, User, user)

        # Should succeed (at least one role remains)
        request.flash.assert_not_called()

        # Verify user still has role2
        user_roles = user.roles
        assert len(user_roles) == 1
        assert user_roles[0].id == role2.id

    def test_user_cannot_remove_all_roles(self, app, client):
        """Test that users cannot remove all their roles (self-protection)."""
        _, admin = app

        # Create user and role
        user = User(email="test@example.com", password="password123", is_admin=True)
        user.save()

        role = Role(name="editor", label="Editor", permissions="posts.view,posts.edit")
        role.save()

        # Assign role
        user.sync("roles", [role.id])

        # Mock request simulating user trying to remove all roles
        request = MagicMock()
        request.user = user
        request.form = {"m2m_roles": ""}  # Empty = remove all roles
        request.flash = MagicMock()

        # Mock translation function
        def mock_t(req, key, **kwargs):
            return key

        admin.t = mock_t

        # Simulate sync
        admin._sync_m2m(request, User, user)

        # Should flash error
        request.flash.assert_called_once_with(
            "error",
            "You cannot remove all your roles. Keep at least one role to maintain access.",
        )

        # Verify user still has the original role (sync was skipped)
        user_roles = user.roles
        assert len(user_roles) == 1
        assert user_roles[0].id == role.id

    def test_regular_user_cannot_change_own_roles(self, app, client):
        """Test that a regular user without roles.edit or is_admin cannot change/escalate their own roles."""
        _, admin = app

        # Create user and roles
        user = User(email="test@example.com", password="password123", is_admin=False)
        user.save()

        role1 = Role(name="editor", label="Editor", permissions="posts.view,posts.edit")
        role1.save()

        role2 = Role(name="viewer", label="Viewer", permissions="posts.view")
        role2.save()

        # Assign role1
        user.sync("roles", [role1.id])

        # Mock request where user attempts to change their role to role2 (or role1 + role2)
        request = MagicMock()
        request.user = user
        request.form = {"m2m_roles": f"{role1.id},{role2.id}"}
        request.flash = MagicMock()

        # Simulate sync (should be blocked by _is_role_sync_authorized returning False)
        admin._sync_m2m(request, User, user)

        # Verify roles did not change
        user_roles = user.roles
        assert len(user_roles) == 1
        assert user_roles[0].id == role1.id

    def test_other_users_can_have_roles_removed(self, app, client):
        """Test that admins can remove all roles from other users."""
        _, admin_ext = app

        # Create admin and regular user
        admin_user = User(email="admin@example.com", password="admin123", is_admin=True)
        admin_user.save()

        regular_user = User(email="user@example.com", password="password123")
        regular_user.save()

        role = Role(name="editor", label="Editor", permissions="posts.view,posts.edit")
        role.save()

        # Assign role to regular user
        regular_user.sync("roles", [role.id])

        # Mock request where admin is editing another user and removing all roles
        request = MagicMock()
        request.user = admin_user  # Admin user
        request.form = {"m2m_roles": ""}  # Remove all roles from regular_user
        request.flash = MagicMock()

        # Simulate sync (editing regular_user, not self)
        admin_ext._sync_m2m(request, User, regular_user)

        # Should NOT flash error (not editing self)
        # The self-protection only applies when editing_self is True

        # In this case, editing_self = False (admin != regular_user)
        # So the roles should be removed normally
        regular_user_roles = regular_user.roles
        assert len(regular_user_roles) == 0  # Roles removed


class TestUserWithoutRoleBlocked:
    """Test that a user without roles (and not is_admin) cannot access or log in to admin."""

    def test_user_without_roles_login_denied(self, app, client):
        """Test that a user without any roles cannot log in via the admin login form."""
        from asok.exceptions import RedirectException

        # Create a user with NO roles and is_admin = False
        user = User(email="norole@example.com", password="password123", is_admin=False)
        user.save()

        # Try to log in. In Asok, if login fails, it returns the rendered page or throws an error.
        # But wait, RedirectException is raised on success. Let's verify it doesn't redirect.
        try:
            client.post(
                "/admin/login",
                data={"email": "norole@example.com", "password": "password123"},
            )
            # If it didn't redirect, it stayed on the login screen, which is correct.
        except RedirectException:
            pytest.fail("Should not redirect to admin dashboard for user without roles")

    def test_user_without_roles_access_denied(self, app):
        """Test that a logged-in user without roles gets blocked when accessing admin routes directly."""
        from asok.exceptions import RedirectException

        _, admin = app

        # Create a user with NO roles
        user = User(email="norole@example.com", password="password123", is_admin=False)
        user.save()

        # Simulate direct request with this user session (bypassing login form restriction)
        request = MagicMock()
        request.user = user
        request.impersonator = None
        request.flash = MagicMock()
        request.session = {}

        # Accessing admin should raise RedirectException to the login page
        with pytest.raises(RedirectException) as excinfo:
            admin._require_admin(request)
        assert "/admin/login" in excinfo.value.url
