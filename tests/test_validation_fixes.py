"""Tests for validation fixes: checkbox handling, enum nullable, color validation."""

import enum
from io import BytesIO

import pytest

from asok import Field, Form, Model
from asok.request import Request
from asok.validation import Validator


class Priority(enum.Enum):
    """Test enum for priority field."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Task(Model):
    """Test model with various field types."""

    _db_path = ":memory:"
    title = Field.String(nullable=False)
    is_completed = Field.Boolean(default=False)
    priority = Field.Enum(Priority, nullable=True)  # Nullable enum
    color = Field.Color(nullable=True)


def test_checkbox_unchecked_converts_to_zero():
    """Test that unchecked checkboxes are converted to '0' not empty string."""
    # Simulate a POST request with checkbox unchecked (not present in form data)
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(b""),
    }
    request = Request(environ)

    schema = {"is_completed": Form.checkbox("Completed", "")}
    form = Form(schema, request)

    # Unchecked checkbox should be "0", not ""
    assert form.is_completed.value == "0"


def test_checkbox_checked_converts_to_one():
    """Test that checked checkboxes are converted to '1'."""
    # Simulate a POST request with checkbox checked
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "15",
        "wsgi.input": BytesIO(b"is_completed=on"),
    }
    request = Request(environ)

    schema = {"is_completed": Form.checkbox("Completed", "")}
    form = Form(schema, request)

    # Checked checkbox should be "1"
    assert form.is_completed.value == "1"


def test_boolean_field_stored_as_integer():
    """Test that boolean fields are stored as integers (0 or 1) in the model."""
    Task.create_table()

    # Create task with boolean as string "1"
    task1 = Task.create(title="Test 1", is_completed="1")
    assert task1.is_completed == 1
    assert isinstance(task1.is_completed, int)

    # Create task with boolean as string "0"
    task2 = Task.create(title="Test 2", is_completed="0")
    assert task2.is_completed == 0
    assert isinstance(task2.is_completed, int)

    # Create task with boolean as empty string ""
    task3 = Task.create(title="Test 3", is_completed="")
    assert task3.is_completed == 0
    assert isinstance(task3.is_completed, int)

    # Create task with boolean as actual bool
    task4 = Task.create(title="Test 4", is_completed=True)
    assert task4.is_completed == 1
    assert isinstance(task4.is_completed, int)

    Task.close_connections()


def test_enum_nullable_allows_empty_value():
    """Test that nullable enum fields accept empty values."""
    data = {"priority": ""}  # Empty value for nullable enum

    v = Validator(data)
    # The "in" rule should allow empty values for nullable fields
    result = v.rule("priority", "in:low,medium,high")

    assert result is True
    assert len(v.errors) == 0


def test_enum_nullable_rejects_invalid_value():
    """Test that nullable enum fields reject invalid non-empty values."""
    data = {"priority": "invalid"}

    v = Validator(data)
    result = v.rule("priority", "in:low,medium,high")

    assert result is False
    assert "priority" in v.errors


def test_enum_nullable_accepts_valid_value():
    """Test that nullable enum fields accept valid values."""
    data = {"priority": "high"}

    v = Validator(data)
    result = v.rule("priority", "in:low,medium,high")

    assert result is True
    assert len(v.errors) == 0


def test_color_validation_valid_6_digit():
    """Test that color validation accepts valid 6-digit hex colors."""
    data = {"color": "#FF5733"}

    v = Validator(data)
    result = v.rule("color", "color")

    assert result is True
    assert len(v.errors) == 0


def test_color_validation_valid_3_digit():
    """Test that color validation accepts valid 3-digit hex colors."""
    data = {"color": "#F00"}

    v = Validator(data)
    result = v.rule("color", "color")

    assert result is True
    assert len(v.errors) == 0


def test_color_validation_invalid():
    """Test that color validation rejects invalid color formats."""
    test_cases = [
        {"color": "FF5733"},  # Missing #
        {"color": "#GG5733"},  # Invalid hex
        {"color": "#FF57"},  # Wrong length
        {"color": "red"},  # Named color (not supported)
        {"color": "#FF57333"},  # Too long
    ]

    for data in test_cases:
        v = Validator(data)
        result = v.rule("color", "color")
        assert result is False, f"Should reject {data['color']}"
        assert "color" in v.errors


def test_color_validation_empty_allowed():
    """Test that color validation allows empty values (for nullable fields)."""
    data = {"color": ""}

    v = Validator(data)
    result = v.rule("color", "color")

    assert result is True
    assert len(v.errors) == 0


def test_form_from_model_color_has_validation():
    """Test that Form.from_model adds 'color' validation rule automatically."""
    Task.create_table()

    form = Form.from_model(Task)

    # Check that the color field has the 'color' validation rule
    assert hasattr(form, "color")
    color_field = form.color
    assert "color" in color_field.rules

    Task.close_connections()


def test_checkbox_form_data_integration():
    """Integration test: checkbox → form.data → model.create."""
    Task.create_table()

    # Simulate POST request with checkbox checked
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "34",
        "wsgi.input": BytesIO(b"title=Test&is_completed=on"),
    }
    request = Request(environ)

    schema = {
        "title": Form.text("Title", "required"),
        "is_completed": Form.checkbox("Completed", ""),
    }
    form = Form(schema, request)

    # Create model from form data
    task = Task.create(**form.data)

    # Should be stored as integer 1
    assert task.is_completed == 1
    assert isinstance(task.is_completed, int)

    Task.close_connections()


def test_enum_nullable_form_integration():
    """Integration test: nullable enum with empty value."""
    Task.create_table()

    # Simulate POST with empty priority (nullable enum)
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "11",
        "wsgi.input": BytesIO(b"title=Test&priority="),
    }
    request = Request(environ)

    # Generate form from model
    form = Form.from_model(Task, request)

    # Validation should pass even with empty priority
    assert form.validate(csrf=False) is True

    Task.close_connections()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
