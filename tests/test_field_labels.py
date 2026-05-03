"""Tests for Field labels and custom error messages."""

import os
import tempfile

from asok.forms import Form
from asok.orm import Field, Model
from asok.request import Request


def test_field_with_label():
    """Test that Field can have a custom label."""
    field = Field.String(max_length=100, label="Full Name")
    assert field.label == "Full Name"


def test_field_with_messages():
    """Test that Field can have custom error messages."""
    field = Field.String(
        max_length=100,
        nullable=False,
        messages={
            "required": "This field is mandatory",
            "max": "Too long!",
        },
    )
    assert field.messages["required"] == "This field is mandatory"
    assert field.messages["max"] == "Too long!"


def test_field_without_label():
    """Test that Field without label has None as default."""
    field = Field.String(max_length=100)
    assert field.label is None


def test_form_from_model_uses_custom_label():
    """Test that Form.from_model uses custom labels from fields."""
    # Create temporary database
    db_fd, db_path = tempfile.mkstemp()

    class TestModel(Model):
        _db_path = db_path
        __tablename__ = "test_model"

        name = Field.String(max_length=100, nullable=False, label="Full Name")
        email = Field.Email(max_length=100, label="Email Address")

    TestModel.create_table()

    # Create form from model
    form = Form.from_model(TestModel)

    # Check that labels are used
    assert form.name._label == "Full Name"
    assert form.email._label == "Email Address"

    # Cleanup
    os.close(db_fd)
    os.unlink(db_path)


def test_form_from_model_uses_field_name_when_no_label():
    """Test that Form.from_model generates label from field name when no custom label."""
    db_fd, db_path = tempfile.mkstemp()

    class TestModel(Model):
        _db_path = db_path
        __tablename__ = "test_model"

        first_name = Field.String(max_length=100)  # No custom label

    TestModel.create_table()

    form = Form.from_model(TestModel)

    # Should generate "First Name" from "first_name"
    assert form.first_name._label == "First Name"

    os.close(db_fd)
    os.unlink(db_path)


def test_form_from_model_uses_custom_messages():
    """Test that Form.from_model uses custom error messages from fields."""
    db_fd, db_path = tempfile.mkstemp()

    class TestModel(Model):
        _db_path = db_path
        __tablename__ = "test_model"

        email = Field.Email(
            max_length=100,
            nullable=False,
            messages={
                "required": "Email is mandatory",
                "email": "Invalid email format",
            },
        )

    TestModel.create_table()

    form = Form.from_model(TestModel)

    # Check that custom messages are passed to form field
    assert form.email.messages["required"] == "Email is mandatory"
    assert form.email.messages["email"] == "Invalid email format"

    os.close(db_fd)
    os.unlink(db_path)


def test_form_validation_uses_custom_messages(app):
    """Test that form validation displays custom error messages."""
    db_fd, db_path = tempfile.mkstemp()

    class TestModel(Model):
        _db_path = db_path
        __tablename__ = "test_model"

        name = Field.String(
            max_length=10,
            nullable=False,
            messages={
                "required": "Name cannot be empty",
                "max": "Name is too long (max 10 chars)",
            },
        )

    TestModel.create_table()

    # Simulate POST request with empty name
    # Simulate POST request with empty name
    # Create a request object
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/test",
        "wsgi.input": None,
    }

    from io import BytesIO

    # Empty form data
    data = b"name="
    environ["wsgi.input"] = BytesIO(data)
    environ["CONTENT_LENGTH"] = str(len(data))
    environ["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
    environ["asok.app"] = app

    request = Request(environ)

    form = Form.from_model(TestModel, request)
    is_valid = form.validate(csrf=False)

    assert not is_valid
    assert "Name cannot be empty" in form.name._error

    # Test max length error
    data = b"name=ThisNameIsTooLong"
    environ["wsgi.input"] = BytesIO(data)
    environ["CONTENT_LENGTH"] = str(len(data))

    request = Request(environ)
    form = Form.from_model(TestModel, request)
    is_valid = form.validate(csrf=False)

    assert not is_valid
    assert "Name is too long" in form.name._error or "max" in form.name._error.lower()

    os.close(db_fd)
    os.unlink(db_path)


def test_all_field_types_support_labels():
    """Test that all Field types support label parameter."""
    # Test various field types
    assert Field.String(label="Test").label == "Test"
    assert Field.Text(label="Test").label == "Test"
    assert Field.Email(label="Test").label == "Test"
    assert Field.Tel(label="Test").label == "Test"
    assert Field.Integer(label="Test").label == "Test"
    assert Field.Boolean(label="Test").label == "Test"
    assert Field.Float(label="Test").label == "Test"
    assert Field.Date(label="Test").label == "Test"
    assert Field.DateTime(label="Test").label == "Test"
    assert Field.Time(label="Test").label == "Test"
    assert Field.File(label="Test").label == "Test"
    assert Field.JSON(label="Test").label == "Test"
    assert Field.Decimal(label="Test").label == "Test"
    assert Field.UUID(label="Test").label == "Test"
    assert Field.Slug(label="Test").label == "Test"
    assert Field.URL(label="Test").label == "Test"
    assert Field.Color(label="Test").label == "Test"
    assert Field.Vector(dimensions=128, label="Test").label == "Test"


def test_all_field_types_support_messages():
    """Test that all Field types support messages parameter."""
    msgs = {"required": "Custom message"}

    assert Field.String(messages=msgs).messages == msgs
    assert Field.Text(messages=msgs).messages == msgs
    assert Field.Email(messages=msgs).messages == msgs
    assert Field.Tel(messages=msgs).messages == msgs
    assert Field.Integer(messages=msgs).messages == msgs
    assert Field.Boolean(messages=msgs).messages == msgs
    assert Field.Float(messages=msgs).messages == msgs
    assert Field.Date(messages=msgs).messages == msgs
    assert Field.DateTime(messages=msgs).messages == msgs
    assert Field.Time(messages=msgs).messages == msgs
    assert Field.File(messages=msgs).messages == msgs
    assert Field.JSON(messages=msgs).messages == msgs
    assert Field.Decimal(messages=msgs).messages == msgs
    assert Field.UUID(messages=msgs).messages == msgs
    assert Field.Slug(messages=msgs).messages == msgs
    assert Field.URL(messages=msgs).messages == msgs
    assert Field.Color(messages=msgs).messages == msgs
    assert Field.Vector(dimensions=128, messages=msgs).messages == msgs
