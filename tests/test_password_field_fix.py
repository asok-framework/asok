"""Test that the password field bug is fixed."""

import os
import tempfile

from asok.orm import Field, Model


class TestUser(Model):
    """Test user model with password field."""

    email = Field.Email(unique=True, nullable=False)
    password = Field.Password(nullable=False)
    name = Field.String(max_length=100)


def test_password_field_creation_without_trust():
    """Test that password fields can be assigned during User.create() without _trust=True."""
    # Setup temporary database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        TestUser._db_path = db_path
        TestUser.create_table()

        # This should work now (previously required _trust=True)
        user = TestUser.create(
            email="test@example.com", password="secret123", name="Test User"
        )

        # Verify user was created
        assert user.id is not None
        assert user.email == "test@example.com"
        assert user.name == "Test User"

        # Verify password was hashed (not stored in plain text)
        assert user.password is not None
        assert user.password != "secret123"
        assert user.password.startswith("pbkdf2:")

        # Verify password can be checked
        assert user.check_password("password", "secret123") is True
        assert user.check_password("password", "wrong") is False

    finally:
        # Cleanup
        TestUser.close_connections()
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_password_field_protected_on_update():
    """Test that password fields are still protected during mass assignment updates."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        TestUser._db_path = db_path
        TestUser.create_table()

        # Create user
        user = TestUser.create(
            email="test2@example.com", password="secret123", name="Test User 2"
        )

        original_password = user.password

        # Try to update password via mass assignment (should be blocked)
        user.update(password="newpassword", name="Updated Name")

        # Name should be updated
        assert user.name == "Updated Name"

        # Password should NOT be updated (protected field)
        assert user.password == original_password

    finally:
        TestUser.close_connections()
        if os.path.exists(db_path):
            os.unlink(db_path)


def test_password_field_can_be_set_directly():
    """Test that password can still be set directly and will be hashed on save."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        TestUser._db_path = db_path
        TestUser.create_table()

        # Create user
        user = TestUser.create(
            email="test3@example.com", password="secret123", name="Test User 3"
        )

        # Change password directly
        user.password = "newsecret456"
        user.save()

        # Verify new password was hashed
        assert user.password != "newsecret456"
        assert user.password.startswith("pbkdf2:")
        assert user.check_password("password", "newsecret456") is True
        assert user.check_password("password", "secret123") is False

    finally:
        TestUser.close_connections()
        if os.path.exists(db_path):
            os.unlink(db_path)


if __name__ == "__main__":
    # Run tests
    print("Testing password field fix...")
    test_password_field_creation_without_trust()
    print("✓ Password creation without _trust=True works!")

    test_password_field_protected_on_update()
    print("✓ Password field still protected on mass assignment update!")

    test_password_field_can_be_set_directly()
    print("✓ Password can be set directly and is hashed on save!")

    print("\n🎉 All tests passed! The password field bug is fixed.")
