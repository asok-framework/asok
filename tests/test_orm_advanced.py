"""
Tests for Advanced ORM features in Asok.
Includes:
- Nested Eager Loading
- Global Scopes (and Soft Delete integration)
- Polymorphic Relationships (MorphTo/MorphMany with eager loading)
- Nested Savepoint Transactions
"""

import pytest

from asok.orm import Field, Model, Relation

# ---------------------------------------------------------------------------
# Models for Testing Nested Eager Loading
# ---------------------------------------------------------------------------


class Company(Model):
    name = Field.String()
    departments = Relation.HasMany("Department")


class Department(Model):
    name = Field.String()
    company_id = Field.ForeignKey("Company")
    employees = Relation.HasMany("Employee")


class Employee(Model):
    name = Field.String()
    department_id = Field.ForeignKey("Department")


# ---------------------------------------------------------------------------
# Models for Testing Global Scopes
# ---------------------------------------------------------------------------


class Product(Model):
    name = Field.String()
    active = Field.Integer(default=1)
    deleted_at = Field.SoftDelete()

    _global_scopes = {
        "active": lambda q: q.where("active", 1)
    }


# ---------------------------------------------------------------------------
# Models for Testing Polymorphic Relationships
# ---------------------------------------------------------------------------


class Comment(Model):
    body = Field.Text()
    commentable_id = Field.Integer()
    commentable_type = Field.String()

    commentable = Relation.MorphTo()


class Article(Model):
    title = Field.String()
    comments = Relation.MorphMany("Comment", "commentable")


class Video(Model):
    title = Field.String()
    comments = Relation.MorphMany("Comment", "commentable")


# ---------------------------------------------------------------------------
# Fixture: DB Setup
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_advanced.db")

    # Close any existing connections
    for model in [Company, Department, Employee, Product, Comment, Article, Video]:
        model.close_connections()
        monkeypatch.setattr(model, "_db_path", db_path)

    # Create tables
    Company.create_table()
    Department.create_table()
    Employee.create_table()
    Product.create_table()
    Comment.create_table()
    Article.create_table()
    Video.create_table()

    yield db_path

    for model in [Company, Department, Employee, Product, Comment, Article, Video]:
        model.close_connections()


# ---------------------------------------------------------------------------
# 1. Test Nested Eager Loading
# ---------------------------------------------------------------------------


def test_nested_eager_loading():
    # Setup hierarchy
    c1 = Company.create(name="TechCorp")
    c2 = Company.create(name="BioCorp")

    d1 = Department.create(name="Engineering", company_id=c1.id)
    d2 = Department.create(name="HR", company_id=c1.id)
    d3 = Department.create(name="R&D", company_id=c2.id)

    Employee.create(name="Alice", department_id=d1.id)
    Employee.create(name="Bob", department_id=d1.id)
    Employee.create(name="Charlie", department_id=d2.id)
    Employee.create(name="Diana", department_id=d3.id)

    # Perform nested eager loading query
    companies = Company.query().with_("departments.employees").get()
    assert len(companies) == 2

    # Verify Company 1 (TechCorp)
    tech = [c for c in companies if c.name == "TechCorp"][0]
    # Check that departments are loaded in cache
    assert "_eager_departments" in tech.__dict__
    departments = tech.departments
    assert len(departments) == 2

    # Check engineering employees
    eng = [d for d in departments if d.name == "Engineering"][0]
    assert "_eager_employees" in eng.__dict__
    assert len(eng.employees) == 2
    assert {e.name for e in eng.employees} == {"Alice", "Bob"}

    # Check HR employees
    hr = [d for d in departments if d.name == "HR"][0]
    assert "_eager_employees" in hr.__dict__
    assert len(hr.employees) == 1
    assert hr.employees[0].name == "Charlie"


# ---------------------------------------------------------------------------
# 2. Test Global Scopes & Soft Delete
# ---------------------------------------------------------------------------


def test_global_scopes():
    # Setup products
    Product.create(name="Laptop", active=1)
    Product.create(name="Phone", active=1)
    Product.create(name="Tablet", active=0) # Inactive

    # Standard query should automatically filter active=1
    products = Product.query().get()
    assert len(products) == 2
    assert {p.name for p in products} == {"Laptop", "Phone"}

    # Query without the 'active' global scope
    all_products = Product.query().without_global_scope("active").get()
    assert len(all_products) == 3
    assert {p.name for p in all_products} == {"Laptop", "Phone", "Tablet"}


def test_global_scopes_soft_delete():
    Product.create(name="Laptop", active=1)
    p2 = Product.create(name="Phone", active=1)

    # Soft delete Phone
    p2.delete()

    # Standard query should filter soft-deleted (soft_delete global scope)
    products = Product.query().get()
    assert len(products) == 1
    assert products[0].name == "Laptop"

    # Query with_trashed() (disables soft_delete scope)
    all_products = Product.query().with_trashed().get()
    assert len(all_products) == 2
    assert {p.name for p in all_products} == {"Laptop", "Phone"}


# ---------------------------------------------------------------------------
# 3. Test Polymorphic Relationships
# ---------------------------------------------------------------------------


def test_polymorphic_relationships():
    # Create target models
    article = Article.create(title="Introduction to Asok")
    video = Video.create(title="Asok Tutorial Video")

    # Create comment pointing to Article (polymorphic)
    c1 = Comment.create(body="Great article!", commentable_id=article.id, commentable_type="Article")
    # Create comment pointing to Video (polymorphic)
    c2 = Comment.create(body="Nice tutorial!", commentable_id=video.id, commentable_type="Video")

    # 1. Test MorphTo property resolution
    assert c1.commentable is not None
    assert isinstance(c1.commentable, Article)
    assert c1.commentable.title == "Introduction to Asok"

    assert c2.commentable is not None
    assert isinstance(c2.commentable, Video)
    assert c2.commentable.title == "Asok Tutorial Video"

    # 2. Test MorphMany property resolution
    assert len(article.comments) == 1
    assert article.comments[0].body == "Great article!"

    assert len(video.comments) == 1
    assert video.comments[0].body == "Nice tutorial!"

    # 3. Test eager loading MorphMany
    articles = Article.query().with_("comments").get()
    assert len(articles) == 1
    assert "_eager_comments" in articles[0].__dict__
    assert len(articles[0].comments) == 1
    assert articles[0].comments[0].body == "Great article!"

    # 4. Test eager loading MorphTo (polymorphic eager loading)
    comments = Comment.query().with_("commentable").get()
    assert len(comments) == 2
    for c in comments:
        assert "_eager_commentable" in c.__dict__
        assert c.commentable is not None
        if c.body == "Great article!":
            assert isinstance(c.commentable, Article)
        else:
            assert isinstance(c.commentable, Video)


# ---------------------------------------------------------------------------
# 4. Test Nested Transactions (Savepoints)
# ---------------------------------------------------------------------------


def test_nested_transactions_savepoint_rollback():
    # Start outer transaction
    with Company.transaction():
        Company.create(name="MainCorp")

        # Nested transaction rolls back
        try:
            with Company.transaction():
                Company.create(name="SubCorp")
                raise ValueError("Rollback sub operation")
        except ValueError:
            pass

        # Outer transaction creates another company and commits
        Company.create(name="AnotherCorp")

    # Verify that MainCorp and AnotherCorp exist, but SubCorp does not!
    companies = Company.query().get()
    assert len(companies) == 2
    assert {c.name for c in companies} == {"MainCorp", "AnotherCorp"}


def test_nested_transactions_full_rollback():
    try:
        with Company.transaction():
            Company.create(name="MainCorp")

            # Nested transaction commits internally
            with Company.transaction():
                Company.create(name="SubCorp")

            # Outer transaction fails and rolls back everything
            raise ValueError("Rollback everything")
    except ValueError:
        pass

    # Verify nothing was saved
    assert Company.count() == 0


# ---------------------------------------------------------------------------
# 5. Test ORM Fixtures (dumpdata / loaddata)
# ---------------------------------------------------------------------------


class FixtureTestModel(Model):
    name = Field.String()
    data = Field("BLOB")


def test_orm_fixtures(tmp_path, monkeypatch):
    import json
    import os

    from asok.cli.database import run_dumpdata, run_loaddata

    # Setup the dummy wsgi.py and project structure
    (tmp_path / "wsgi.py").write_text("app = None\n")
    # Change working directory so _find_project_root works
    monkeypatch.chdir(tmp_path)

    # Re-initialize/monkeypatch our models' db path
    db_path = str(tmp_path / "test_fixtures.db")
    FixtureTestModel.close_connections()
    monkeypatch.setattr(FixtureTestModel, "_db_path", db_path)
    FixtureTestModel.create_table()

    # Insert initial test records
    # Include binary data (bytes)
    m1 = FixtureTestModel.create(name="BinaryRecord", data=b"\x00\x01\x02\x03\xff")
    m2 = FixtureTestModel.create(name="SecondRecord", data=b"hello")

    # 1. Test dumpdata
    fixture_file = str(tmp_path / "fixture.json")
    run_dumpdata(model_name="FixtureTestModel", output_file=fixture_file)

    # Verify JSON structure
    assert os.path.exists(fixture_file)
    with open(fixture_file, "r") as f:
        data = json.load(f)

    assert len(data) == 2
    assert data[0]["model"] == "FixtureTestModel"
    assert data[0]["fields"]["name"] == "BinaryRecord"
    # Verify binary data base64 format
    assert data[0]["fields"]["data"].startswith("base64:")

    # 2. Test loaddata (updating existing, inserting new)
    # Let's modify the fixture file to:
    # - Update m1's name and data
    # - Add a new record with pk=3 (which doesn't exist)
    data[0]["fields"]["name"] = "BinaryRecordUpdated"
    data[0]["fields"]["data"] = "base64:c29tZXRoaW5nIG5ldw=="  # base64 for b"something new"
    data.append({
        "model": "FixtureTestModel",
        "pk": 3,
        "fields": {
            "name": "ThirdRecord",
            "data": "base64:dGVzdA=="  # base64 for b"test"
        }
    })

    with open(fixture_file, "w") as f:
        json.dump(data, f)

    # Run loaddata
    run_loaddata(fixture_file)

    # Verify existing record (m1) was updated
    m1_updated = FixtureTestModel.find(id=m1.id)
    assert m1_updated is not None
    assert m1_updated.name == "BinaryRecordUpdated"
    assert m1_updated.data == b"something new"

    # Verify new record (pk=3) was inserted and PK was preserved
    m3 = FixtureTestModel.find(id=3)
    assert m3 is not None
    assert m3.name == "ThirdRecord"
    assert m3.data == b"test"

    # Verify other records (m2) were not broken
    m2_check = FixtureTestModel.find(id=m2.id)
    assert m2_check is not None
    assert m2_check.name == "SecondRecord"
    assert m2_check.data == b"hello"

    FixtureTestModel.close_connections()

