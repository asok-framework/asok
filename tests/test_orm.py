"""
Tests for the ORM.
Uses the real Asok ORM API:
  - Model.find(id=x)             — find by PK
  - Model.all(name="Alice")      — filter via all(**kwargs)
  - Model.where("col", val)      — fluent query builder
  - Model.paginate(page, per_page)
  - instance.update(**kwargs)
  - instance.delete()
  - instance.check_password(plain)
  - instance.to_dict()
"""

import pytest

from asok.orm import Field, Model

# ---------------------------------------------------------------------------
# Models defined at module level (processed by ModelMeta once)
# ---------------------------------------------------------------------------


class User(Model):
    name = Field.String()
    email = Field.String(unique=True)
    password = Field.Password()


class Post(Model):
    title = Field.String()
    body = Field.Text(default="")
    author_id = Field.Integer(default=0)
    published = Field.Boolean(default=False)


# ---------------------------------------------------------------------------
# Fixture: fresh temp DB per test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    # Close any existing connections from previous tests
    User.close_connections()
    Post.close_connections()
    monkeypatch.setattr(User, "_db_path", db_path)
    monkeypatch.setattr(Post, "_db_path", db_path)
    User.create_table()
    Post.create_table()
    yield db_path
    User.close_connections()
    Post.close_connections()


# ---------------------------------------------------------------------------
# Helper: create a User (password fields need obj.password = x; obj.save())
# ---------------------------------------------------------------------------


def create_user(name="Alice", email="alice@example.com", password="secret"):
    u = User(name=name, email=email)
    u.password = password
    u.save()
    return u


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCRUD:
    def test_create_and_find(self):
        user = create_user()
        assert user.id is not None
        fetched = User.find(id=user.id)
        assert fetched is not None
        assert fetched.name == "Alice"

    def test_update(self):
        user = create_user()
        user.update(name="Alice Updated")
        fetched = User.find(id=user.id)
        assert fetched.name == "Alice Updated"

    def test_delete(self):
        user = create_user()
        uid = user.id
        user.delete()
        assert User.find(id=uid) is None

    def test_all_returns_all_users(self):
        create_user("Alice", "a@example.com")
        create_user("Bob", "b@example.com")
        users = User.all()
        assert len(users) == 2

    def test_all_with_filter(self):
        create_user("Alice", "a@example.com")
        create_user("Bob", "b@example.com")
        results = User.all(name="Alice")
        assert len(results) == 1
        assert results[0].name == "Alice"

    def test_first(self):
        create_user()
        user = User.query().first()
        assert user is not None
        assert user.name == "Alice"

    def test_count(self):
        create_user("Alice", "a@example.com")
        create_user("Bob", "b@example.com")
        assert User.count() == 2

    def test_exists(self):
        create_user()
        assert User.exists(name="Alice")
        assert not User.exists(name="Nobody")


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


class TestPassword:
    def test_password_is_hashed(self):
        user = create_user()
        fetched = User.find(id=user.id)
        assert fetched.password != "secret"

    def test_check_password_correct(self):
        user = create_user()
        fetched = User.find(id=user.id)
        assert fetched.check_password("password", "secret")

    def test_check_password_wrong(self):
        user = create_user()
        fetched = User.find(id=user.id)
        assert not fetched.check_password("password", "wrongpassword")


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    def test_paginate_first_page(self):
        for i in range(15):
            create_user(f"User{i}", f"u{i}@example.com")
        page = User.paginate(1, 10)
        assert len(page["items"]) == 10
        assert page["total"] == 15
        assert page["current_page"] == 1

    def test_paginate_second_page(self):
        for i in range(15):
            create_user(f"User{i}", f"u{i}@example.com")
        page = User.paginate(2, 10)
        assert len(page["items"]) == 5
        assert page["current_page"] == 2


# ---------------------------------------------------------------------------
# where query builder
# ---------------------------------------------------------------------------


class TestWhere:
    def test_where_equals(self):
        create_user("Alice", "a@example.com")
        create_user("Bob", "b@example.com")
        results = User.where("name", "Alice").get()
        assert len(results) == 1
        assert results[0].name == "Alice"


# ---------------------------------------------------------------------------
# Unique constraint
# ---------------------------------------------------------------------------


class TestUnique:
    def test_unique_field_raises_on_duplicate(self):
        create_user("Alice", "alice@example.com")
        with pytest.raises(Exception):
            create_user("Alice2", "alice@example.com")


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict_returns_dict(self):
        user = create_user()
        d = user.to_dict()
        assert isinstance(d, dict)
        assert d["name"] == "Alice"
        assert "id" in d

    def test_to_dict_excludes_password(self):
        """Password fields (hidden=True) should not appear in to_dict."""
        user = create_user()
        d = user.to_dict()
        # Password is a hidden/protected field
        assert "password" not in d or d.get("password") is None


# ---------------------------------------------------------------------------
# Post model (no password fields — uses normal .create())
# ---------------------------------------------------------------------------


class TestPost:
    def test_create_post(self):
        post = Post.create(title="Hello", body="World", author_id=1)
        assert post.id is not None
        fetched = Post.find(id=post.id)
        assert fetched.title == "Hello"

    def test_post_default_values(self):
        post = Post.create(title="Test")
        fetched = Post.find(id=post.id)
        assert not fetched.published
        assert fetched.body == ""

    def test_post_count(self):
        Post.create(title="Post 1")
        Post.create(title="Post 2")
        assert Post.count() == 2
