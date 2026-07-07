import pytest

from asok.orm import Field, Model


class SimpleUser(Model):
    name = Field.String()
    email = Field.String(unique=True)
    age = Field.Integer(default=0)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    SimpleUser.close_connections()
    monkeypatch.setattr(SimpleUser, "_db_path", db_path)
    SimpleUser.create_table()
    yield db_path
    SimpleUser.close_connections()


def test_orm_filter_by_query_builder():
    # Create test data
    SimpleUser.create(name="Alice", email="alice@example.com", age=25)
    SimpleUser.create(name="Bob", email="bob@example.com", age=30)
    SimpleUser.create(name="Charlie", email="charlie@example.com", age=25)

    # 1. Test Query.filter_by with single match
    alice = SimpleUser.query().filter_by(name="Alice").first()
    assert alice is not None
    assert alice.email == "alice@example.com"
    assert alice.age == 25

    # 2. Test Query.filter_by with multiple matches
    twenty_fives = SimpleUser.query().filter_by(age=25).get()
    assert len(twenty_fives) == 2
    assert {u.name for u in twenty_fives} == {"Alice", "Charlie"}

    # 3. Test multiple key-value pairs in a single filter_by call
    charlie = SimpleUser.query().filter_by(name="Charlie", age=25).first()
    assert charlie is not None
    assert charlie.email == "charlie@example.com"

    # 4. Test chained filter_by calls
    bob = SimpleUser.query().filter_by(name="Bob").filter_by(age=30).first()
    assert bob is not None
    assert bob.email == "bob@example.com"


def test_orm_filter_by_model_classmethod():
    SimpleUser.create(name="Alice", email="alice@example.com", age=25)
    SimpleUser.create(name="Bob", email="bob@example.com", age=30)

    # Test Model.filter_by delegating to Query.filter_by
    alice = SimpleUser.filter_by(name="Alice").first()
    assert alice is not None
    assert alice.email == "alice@example.com"

    bob = SimpleUser.filter_by(name="Bob", age=30).first()
    assert bob is not None
    assert bob.email == "bob@example.com"


def test_orm_filter_by_invalid_column():
    # Filtering on an invalid column should raise a ValueError
    with pytest.raises(ValueError, match="Invalid column: non_existent"):
        SimpleUser.filter_by(non_existent="val")
