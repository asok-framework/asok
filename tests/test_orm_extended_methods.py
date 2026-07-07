import pytest

from asok.orm import Field, Model
from asok.orm.exceptions import ModelError


class AdvancedUser(Model):
    name = Field.String()
    email = Field.String(unique=True)
    age = Field.Integer(default=0)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    AdvancedUser.close_connections()
    monkeypatch.setattr(AdvancedUser, "_db_path", db_path)
    AdvancedUser.create_table()
    yield db_path
    AdvancedUser.close_connections()


def test_first_or_fail():
    AdvancedUser.create(name="Alice", email="alice@example.com", age=25)

    # 1. Success case on query builder
    user = AdvancedUser.query().where("name", "Alice").first_or_fail()
    assert user.email == "alice@example.com"

    # 2. Failure case on query builder -> raises ModelError
    with pytest.raises(ModelError, match="AdvancedUser not found"):
        AdvancedUser.query().where("name", "Bob").first_or_fail()

    # 3. Model classmethod shortcut success
    user2 = AdvancedUser.first_or_fail(name="Alice")
    assert user2.age == 25

    # 4. Model classmethod shortcut failure -> raises ModelError
    with pytest.raises(ModelError, match="AdvancedUser not found"):
        AdvancedUser.first_or_fail(name="Bob")


def test_last():
    # Create unordered test data
    AdvancedUser.create(name="Alice", email="alice@example.com", age=25)
    AdvancedUser.create(name="Bob", email="bob@example.com", age=30)
    AdvancedUser.create(name="Charlie", email="charlie@example.com", age=25)

    # 1. Default ordering last() (uses id DESC -> returns Charlie)
    last_user = AdvancedUser.query().last()
    assert last_user is not None
    assert last_user.name == "Charlie"

    # 2. Ordered last() (query is order_by('name') -> reverses to 'name DESC' -> returns Charlie)
    last_user_alpha = AdvancedUser.query().order_by("name").last()
    assert last_user_alpha is not None
    assert last_user_alpha.name == "Charlie"

    # 3. Ordered last() with DESC (query is order_by('-name') -> reverses to 'name ASC' -> returns Alice)
    last_user_desc = AdvancedUser.query().order_by("-name").last()
    assert last_user_desc is not None
    assert last_user_desc.name == "Alice"

    # 4. Last on query with no results -> None
    no_user = AdvancedUser.query().where("name", "Dave").last()
    assert no_user is None


def test_first_or_create_on_query():
    # 1. Should create when not existing
    user = AdvancedUser.query().first_or_create(
        defaults={"age": 40}, name="Eve", email="eve@example.com"
    )
    assert user.id is not None
    assert user.name == "Eve"
    assert user.age == 40

    # Verify database state
    db_user = AdvancedUser.find(name="Eve")
    assert db_user is not None
    assert db_user.age == 40

    # 2. Should return existing when match found
    retrieved = AdvancedUser.query().first_or_create(defaults={"age": 50}, name="Eve")
    assert retrieved.id == user.id
    assert retrieved.age == 40  # Defaults should NOT be applied since record exists


def test_update_or_create_on_query():
    # 1. Should create when not existing
    user = AdvancedUser.query().update_or_create(
        defaults={"age": 40}, name="Eve", email="eve@example.com"
    )
    assert user.id is not None
    assert user.name == "Eve"
    assert user.age == 40

    # 2. Should update defaults when existing match found
    retrieved = AdvancedUser.query().update_or_create(defaults={"age": 50}, name="Eve")
    assert retrieved.id == user.id
    assert retrieved.age == 50  # Defaults should be applied/updated
