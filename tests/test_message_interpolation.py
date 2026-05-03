"""Tests for improved error message interpolation with specific placeholders."""

import pytest

from asok.validation import Validator


def test_min_placeholder():
    """Test that {min} placeholder is interpolated correctly."""
    data = {"name": "ab"}
    v = Validator(data)
    result = v.rule("name", "min:5", {"min": "Must be at least {min} characters"})

    assert result is False
    assert "name" in v.errors
    assert v.errors["name"] == "Must be at least 5 characters"


def test_max_placeholder():
    """Test that {max} placeholder is interpolated correctly."""
    data = {"name": "a" * 101}
    v = Validator(data)
    result = v.rule("name", "max:100", {"max": "Maximum {max} characters allowed"})

    assert result is False
    assert "name" in v.errors
    assert v.errors["name"] == "Maximum 100 characters allowed"


def test_between_placeholders():
    """Test that {min} and {max} placeholders work with between rule."""
    data = {"age": "5"}
    v = Validator(data)
    result = v.rule("age", "between:10,99", {"between": "Must be between {min} and {max}"})

    assert result is False
    assert "age" in v.errors
    assert v.errors["age"] == "Must be between 10 and 99"


def test_ext_placeholder():
    """Test that {extensions} placeholder is interpolated."""
    # Simulated file data
    class FakeFile:
        filename = "test.txt"
        content = b"test"

    data = {}
    files = {"document": FakeFile()}
    v = Validator(data, files)
    result = v.rule("document", "ext:pdf,docx", {"ext": "Only {extensions} files allowed"})

    assert result is False
    assert "document" in v.errors
    assert v.errors["document"] == "Only pdf,docx files allowed"


def test_field_placeholder():
    """Test that {field} placeholder shows field name."""
    data = {"user_name": "ab"}
    v = Validator(data)
    result = v.rule("user_name", "min:5", {"min": "{field} must have at least {min} characters"})

    assert result is False
    assert "user_name" in v.errors
    assert v.errors["user_name"] == "User Name must have at least 5 characters"


def test_same_placeholder():
    """Test that {other} placeholder works with same rule."""
    data = {"password": "secret123", "password_confirmation": "different"}
    v = Validator(data)
    result = v.rule("password", "same:password_confirmation", {
        "same": "Password must match {other}"
    })

    assert result is False
    assert "password" in v.errors
    assert v.errors["password"] == "Password must match Password Confirmation"


def test_in_placeholder():
    """Test that {values} placeholder works with in rule."""
    data = {"status": "pending"}
    v = Validator(data)
    result = v.rule("status", "in:active,inactive", {
        "in": "Status must be one of: {values}"
    })

    assert result is False
    assert "status" in v.errors
    assert v.errors["status"] == "Status must be one of: active,inactive"


def test_backward_compatibility_arg():
    """Test that old {arg} placeholder still works for backward compatibility."""
    data = {"name": "ab"}
    v = Validator(data)
    result = v.rule("name", "min:5", {"min": "At least {arg} chars required"})

    assert result is False
    assert "name" in v.errors
    assert v.errors["name"] == "At least 5 chars required"


def test_default_messages_use_new_placeholders():
    """Test that default messages use the new specific placeholders."""
    # Test min default message
    data = {"name": "ab"}
    v = Validator(data)
    v.rule("name", "min:5")  # No custom message, use default

    assert "name" in v.errors
    assert "5" in v.errors["name"]  # Should interpolate {min}
    assert "Minimum 5 characters" in v.errors["name"]

    # Test max default message
    data2 = {"bio": "a" * 501}
    v2 = Validator(data2)
    v2.rule("bio", "max:500")

    assert "bio" in v2.errors
    assert "500" in v2.errors["bio"]  # Should interpolate {max}
    assert "Maximum 500 characters" in v2.errors["bio"]


def test_between_default_message():
    """Test that between rule's default message shows both min and max."""
    data = {"age": "5"}
    v = Validator(data)
    v.rule("age", "between:18,65")  # No custom message

    assert "age" in v.errors
    assert "18" in v.errors["age"]
    assert "65" in v.errors["age"]
    assert "between 18 and 65" in v.errors["age"].lower()


def test_multiple_placeholders_in_one_message():
    """Test using multiple placeholders in a single message."""
    data = {"username": "a"}
    v = Validator(data)
    result = v.rule("username", "min:3", {
        "min": "{field} needs at least {min} characters (you entered {arg})"
    })

    assert result is False
    assert v.errors["username"] == "Username needs at least 3 characters (you entered 3)"


def test_missing_placeholder_not_replaced():
    """Test that unknown placeholders are left as-is."""
    data = {"name": "ab"}
    v = Validator(data)
    result = v.rule("name", "min:5", {"min": "Error: {unknown_placeholder}"})

    assert result is False
    assert v.errors["name"] == "Error: {unknown_placeholder}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
