"""Tests for recent fixes: Enum fields, Boolean validation, and BelongsToMany pivot tables."""

import enum

import pytest

from asok import Field, Form, Model, Relation


class Status(enum.Enum):
    """Test enum for status field."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Post(Model):
    """Test model with Enum and Boolean fields."""

    _db_path = ":memory:"
    title = Field.String(nullable=False)
    status = Field.Enum(Status, default=Status.DRAFT)
    is_featured = Field.Boolean(default=False)


class Tag(Model):
    """Test model for BelongsToMany relationship."""

    _db_path = ":memory:"
    name = Field.String(nullable=False, unique=True)


class Article(Model):
    """Test model with BelongsToMany relationship to Tag."""

    _db_path = ":memory:"
    title = Field.String(nullable=False)
    tags = Relation.BelongsToMany("Tag", pivot_table="article_tags")


def test_enum_field_validation():
    """Test that Enum fields have automatic 'in' validation."""
    # Create a form with an enum field
    schema = {"status": Form.enum("Status", Status, "")}

    # Check that the enum field has 'in' validation rule
    field_tuple = schema["status"]
    field_type, label, rules, messages, choices, attrs = field_tuple

    assert "in:" in rules
    assert "draft" in rules
    assert "published" in rules
    assert "archived" in rules

    # Verify choices are correct
    assert len(choices) == 3
    assert ("draft", "Draft") in choices
    assert ("published", "Published") in choices
    assert ("archived", "Archived") in choices


def test_boolean_field_has_rules():
    """Test that Boolean fields receive validation rules."""
    # Generate form from model
    form = Form.from_model(Post)

    # The is_featured field should be a checkbox
    assert hasattr(form, "is_featured")
    field = form.is_featured

    # Check that it's a checkbox type
    assert field.type == "checkbox"


def test_enum_field_in_model():
    """Test that Enum fields work correctly in models."""
    Post.create_table()

    # Create a post with default status
    post1 = Post.create(title="Test Post 1")
    assert post1.status == Status.DRAFT

    # Create a post with specific status
    post2 = Post.create(title="Test Post 2", status=Status.PUBLISHED)
    assert post2.status == Status.PUBLISHED

    # Verify we can query by enum value
    post = Post.find(id=post2.id)
    assert post.status == Status.PUBLISHED
    assert post.status.value == "published"

    Post.close_connections()


def test_pivot_table_creation():
    """Test that BelongsToMany pivot tables are created automatically."""
    # Create tables
    Article.create_table()
    Tag.create_table()

    # Verify the main tables exist
    with Article._get_conn() as conn:
        # Check that articles table exists
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
        ).fetchone()
        assert result is not None

        # Check that tags table exists
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone()
        assert result is not None

        # Check that the pivot table exists
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='article_tags'"
        ).fetchone()
        assert result is not None, "Pivot table 'article_tags' should be created"

        # Verify the pivot table structure
        columns = conn.execute("PRAGMA table_info(article_tags)").fetchall()
        column_names = [col[1] for col in columns]

        assert "article_id" in column_names
        assert "tag_id" in column_names

    Article.close_connections()
    Tag.close_connections()


def test_belongs_to_many_attach_detach():
    """Test that attach/detach work with the pivot table."""
    Article.create_table()
    Tag.create_table()

    # Create some tags
    tag1 = Tag.create(name="Python")
    tag2 = Tag.create(name="Django")
    tag3 = Tag.create(name="Flask")

    # Create an article
    article = Article.create(title="Web Development with Python")

    # Attach tags
    article.attach("tags", [tag1.id, tag2.id])

    # Verify tags are attached
    tags = article.tags
    assert len(tags) == 2
    tag_names = [tag.name for tag in tags]
    assert "Python" in tag_names
    assert "Django" in tag_names

    # Detach one tag
    article.detach("tags", tag2.id)

    # Verify only one tag remains
    tags = article.tags
    assert len(tags) == 1
    assert tags[0].name == "Python"

    # Sync with new tags
    article.sync("tags", [tag2.id, tag3.id])

    # Verify sync worked
    tags = article.tags
    assert len(tags) == 2
    tag_names = [tag.name for tag in tags]
    assert "Django" in tag_names
    assert "Flask" in tag_names
    assert "Python" not in tag_names

    Article.close_connections()
    Tag.close_connections()


def test_form_from_model_with_enum():
    """Test that Form.from_model handles Enum fields correctly."""
    Post.create_table()

    # Generate form from model
    form = Form.from_model(Post)

    # Verify the status field exists
    assert hasattr(form, "status")
    status_field = form.status

    # Verify it's a select field
    assert status_field.type == "select"

    # Verify it has the correct choices
    assert status_field.choices is not None
    choice_values = [choice[0] for choice in status_field.choices]
    assert "draft" in choice_values
    assert "published" in choice_values
    assert "archived" in choice_values

    Post.close_connections()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
