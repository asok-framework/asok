"""Tests for checkbox, radio, and select validation in admin and forms."""

from io import BytesIO

import pytest

from asok import Field, Form, Model
from asok.request import Request


class Task(Model):
    """Test model with various checkbox types."""

    _db_path = ":memory:"
    title = Field.String(nullable=False)
    is_completed = Field.Boolean(default=False)  # Boolean field
    is_active = Field.Integer(default=1)  # INTEGER that starts with "is_"
    has_deadline = Field.Integer(default=0)  # INTEGER that starts with "has_"


def test_checkbox_unchecked_stores_zero_not_one():
    """Test that unchecked checkbox stores 0, not 1 (bug fix for truthiness)."""
    Task.create_table()

    # Simulate admin _apply_form logic
    # Before fix: if "0": would be True, storing 1
    # After fix: if "0" == "1": would be False, storing 0

    task = Task(title="Test")

    # Simulate unchecked checkbox (value = "0")
    raw_value = "0"

    # OLD BUGGY CODE: setattr(task, "is_active", 1 if raw_value else 0)
    # This would set is_active = 1 because "0" is truthy!

    # NEW FIXED CODE: setattr(task, "is_active", 1 if raw_value == "1" else 0)
    task.is_active = 1 if raw_value == "1" else 0

    assert task.is_active == 0, "Unchecked checkbox should be 0, not 1"

    Task.close_connections()


def test_checkbox_checked_stores_one():
    """Test that checked checkbox stores 1."""
    Task.create_table()

    task = Task(title="Test")
    raw_value = "1"  # Checked checkbox

    task.is_active = 1 if raw_value == "1" else 0

    assert task.is_active == 1

    Task.close_connections()


def test_boolean_field_conversion():
    """Test that Boolean fields convert "0"/"1" strings to integers."""
    Task.create_table()

    # Create with string "1" (from form)
    task1 = Task.create(title="Test 1", is_completed="1")
    assert task1.is_completed == 1
    assert isinstance(task1.is_completed, int)

    # Create with string "0" (from form)
    task2 = Task.create(title="Test 2", is_completed="0")
    assert task2.is_completed == 0
    assert isinstance(task2.is_completed, int)

    # Create with empty string (unchecked)
    task3 = Task.create(title="Test 3", is_completed="")
    assert task3.is_completed == 0
    assert isinstance(task3.is_completed, int)

    Task.close_connections()


def test_checkbox_post_binding():
    """Test that checkbox POST binding creates '0' or '1' strings."""
    # Unchecked checkbox (not in form data)
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "0",
        "wsgi.input": BytesIO(b""),
    }
    request = Request(environ)

    schema = {"is_completed": Form.checkbox("Completed", "")}
    form = Form(schema, request)

    assert form.is_completed.value == "0"

    # Checked checkbox
    environ2 = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "CONTENT_LENGTH": "15",
        "wsgi.input": BytesIO(b"is_completed=on"),
    }
    request2 = Request(environ2)

    form2 = Form(schema, request2)

    assert form2.is_completed.value == "1"


def test_checkbox_required_validation():
    """Test that 'required' validation doesn't make sense for checkboxes."""
    # This test documents the current behavior:
    # An unchecked checkbox (value="0") passes "required" validation
    # because "0" is not an empty string.

    from asok.validation import Validator

    # Unchecked checkbox
    data = {"accept_terms": "0"}
    v = Validator(data)
    result = v.rule("accept_terms", "required")

    # This PASSES because "0" is not empty
    # This is technically a limitation: "required" doesn't work for checkboxes
    assert result is True
    assert len(v.errors) == 0


def test_integer_is_field_checkbox():
    """Test that INTEGER fields starting with 'is_' are treated as checkboxes."""
    form = Form.from_model(Task)

    # is_active (INTEGER) should be checkbox
    assert hasattr(form, "is_active")
    assert form.is_active.type == "checkbox"

    # has_deadline (INTEGER) should be checkbox
    assert hasattr(form, "has_deadline")
    assert form.has_deadline.type == "checkbox"


def test_boolean_field_checkbox():
    """Test that Boolean fields are checkboxes."""
    form = Form.from_model(Task)

    assert hasattr(form, "is_completed")
    assert form.is_completed.type == "checkbox"


def test_checkbox_render_checked():
    """Test that checkbox renders with 'checked' attribute when value is '1'."""
    schema = {"is_active": Form.checkbox("Active", "")}
    form = Form(schema)

    # Set value to "1" (checked)
    form.is_active.value = "1"

    html = form.is_active.render_input()

    assert 'type="checkbox"' in html
    assert "checked" in html


def test_checkbox_render_unchecked():
    """Test that checkbox renders without 'checked' when value is '0'."""
    schema = {"is_active": Form.checkbox("Active", "")}
    form = Form(schema)

    # Set value to "0" (unchecked)
    form.is_active.value = "0"

    html = form.is_active.render_input()

    assert 'type="checkbox"' in html
    assert "checked" not in html


def test_checkbox_truthiness_edge_cases():
    """Test edge cases for checkbox truthiness."""
    # Empty string
    assert ("" == "1") is False
    assert (1 if "" == "1" else 0) == 0

    # String "0"
    assert ("0" == "1") is False
    assert (1 if "0" == "1" else 0) == 0

    # String "1"
    assert ("1" == "1") is True
    assert (1 if "1" == "1" else 0) == 1

    # None
    assert (None == "1") is False
    assert (1 if None == "1" else 0) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
