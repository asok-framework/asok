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


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------


class TestTransaction:
    def test_transaction_commit(self):
        with User.transaction():
            u = create_user(name="TxCommit", email="txcommit@example.com")
            assert u.id is not None
            assert User.find(id=u.id) is not None

        fetched = User.find(email="txcommit@example.com")
        assert fetched is not None
        assert fetched.name == "TxCommit"

    def test_transaction_rollback(self):
        try:
            with User.transaction():
                create_user(name="TxRollback", email="txrollback@example.com")
                raise ValueError("Forced rollback")
        except ValueError:
            pass

        assert User.find(email="txrollback@example.com") is None


# ---------------------------------------------------------------------------
# Query Cache
# ---------------------------------------------------------------------------


class TestQueryCache:
    def test_query_cache_memory(self):
        create_user("Alice", "alice@example.com")
        # Cache for 60 seconds
        q = User.query().where("name", "Alice").cache(60)
        results = q.get()
        assert len(results) == 1
        assert results[0].name == "Alice"

        # Modify name directly in DB to bypass cache
        User.get_engine().execute("UPDATE users SET name = 'Bob' WHERE email = 'alice@example.com'")

        # Fetch again with caching enabled
        cached_results = User.query().where("name", "Alice").cache(60).get()
        assert len(cached_results) == 1
        # Should return cached Alice
        assert cached_results[0].name == "Alice"

        # Fetch without cache
        fresh_results = User.query().where("name", "Bob").get()
        assert len(fresh_results) == 1
        assert fresh_results[0].name == "Bob"

    def test_query_cache_file_backend(self, tmp_path, monkeypatch):
        from asok.cache import default_cache

        # Change backend to file and set _path to tmp_path / "cache"
        monkeypatch.setattr(default_cache, "backend", "file")
        monkeypatch.setattr(default_cache, "_path", str(tmp_path / "cache"))
        import os
        os.makedirs(default_cache._path, exist_ok=True)

        create_user("Alice", "alice@example.com")
        
        # Verify it serializes and deserializes without error on a file backend
        q = User.query().where("name", "Alice").cache(60)
        results = q.get()
        assert len(results) == 1
        assert results[0].name == "Alice"

        # Modify name in DB
        User.get_engine().execute("UPDATE users SET name = 'Bob' WHERE email = 'alice@example.com'")

        # Fetch again with caching enabled, should read from file cache
        cached_results = User.query().where("name", "Alice").cache(60).get()
        assert len(cached_results) == 1
        assert cached_results[0].name == "Alice"


# ---------------------------------------------------------------------------
# Compound Queries
# ---------------------------------------------------------------------------


class TestCompoundQueries:
    def test_union_and_intersect_aggregates(self):
        # Create some users
        create_user("Alice", "alice@example.com")
        create_user("Bob", "bob@example.com")
        create_user("Charlie", "charlie@example.com")

        q1 = User.query().where("name", "Alice")
        q2 = User.query().where("name", "Bob")
        union_q = q1.union(q2)

        # 1. Test count() on UNION
        assert union_q.count() == 2

        # 2. Test pluck() on UNION
        plucked = union_q.pluck("name")
        assert len(plucked) == 2
        assert "Alice" in plucked
        assert "Bob" in plucked

        # 3. Test sum() on UNION using Post
        Post.create(title="Post 1", author_id=10)
        Post.create(title="Post 2", author_id=20)
        Post.create(title="Post 3", author_id=30)

        pq1 = Post.query().where("title", "Post 1")
        pq2 = Post.query().where("title", "Post 2")
        pq_union = pq1.union(pq2)

        assert pq_union.sum("author_id") == 30

        # 4. Test INTERSECT
        # Users with name Alice OR Bob
        qa = User.query().where("name", "Alice")
        qb = User.query().where("name", "Alice") # matches Alice
        intersect_q = qa.intersect(qb)
        assert intersect_q.count() == 1
        assert intersect_q.pluck("name") == ["Alice"]

    def test_compound_query_write_safeguards(self):
        create_user("Alice", "alice@example.com")
        create_user("Bob", "bob@example.com")

        q1 = User.query().where("name", "Alice")
        q2 = User.query().where("name", "Bob")
        union_q = q1.union(q2)

        # Verify bulk operations raise ValueError
        with pytest.raises(ValueError, match="Cannot update a compound query"):
            union_q.update(name="New Name")

        with pytest.raises(ValueError, match="Cannot delete a compound query"):
            union_q.delete()

        with pytest.raises(ValueError, match="Cannot delete a compound query"):
            union_q.force_delete()
