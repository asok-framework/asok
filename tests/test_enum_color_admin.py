"""Tests for Enum and Color fields in admin and forms."""

import enum

import pytest

from asok import Field, Form, Model


class Language(enum.Enum):
    """Test enum for language field."""

    EN = "en"
    FR = "fr"
    DE = "de"


class Category(Model):
    """Test model with Enum and Color fields."""

    _db_path = ":memory:"
    __tablename__ = "categories"

    name = Field.String(nullable=False, max_length=100)
    locale = Field.Enum(Language, default=Language.EN, nullable=True)
    color = Field.Color(default="#3b82f6", nullable=True)


def test_enum_default_value_applied():
    """Test that Enum default values are correctly applied to new models."""
    Category.create_table()

    # Create category without specifying locale
    cat = Category(name="Test")
    assert cat.locale == Language.EN
    assert isinstance(cat.locale, Language)

    Category.close_connections()


def test_enum_save_with_string_value():
    """Test that saving Enum as string (from form) works correctly."""
    Category.create_table()

    # Simulate form submission with string value
    cat = Category(name="Test")
    cat.locale = "fr"  # String, not Enum object

    # Should not crash on save
    cat.save()

    # Retrieve and verify
    cat_loaded = Category.find(id=cat.id)
    assert cat_loaded.locale == Language.FR
    assert isinstance(cat_loaded.locale, Language)

    Category.close_connections()


def test_enum_save_with_enum_object():
    """Test that saving Enum as Enum object works correctly."""
    Category.create_table()

    # Direct Enum object assignment
    cat = Category(name="Test", locale=Language.DE)
    cat.save()

    # Retrieve and verify
    cat_loaded = Category.find(id=cat.id)
    assert cat_loaded.locale == Language.DE
    assert isinstance(cat_loaded.locale, Language)

    Category.close_connections()


def test_form_fill_extracts_enum_value():
    """Test that form.fill() extracts .value from Enum objects."""
    Category.create_table()

    # Create category with Enum
    cat = Category.create(name="Test", locale=Language.FR)

    # Generate form and fill with category
    form = Form.from_model(Category)
    form.fill(cat)

    # The form field should contain the string value, not the Enum object
    assert form.locale.value == "fr"
    assert not isinstance(form.locale.value, Language)

    Category.close_connections()


def test_form_fill_enum_default_on_new_model():
    """Test that form.fill() shows default Enum value for new models."""
    Category.create_table()

    # Create new category (no id yet)
    cat = Category(name="Test")

    # Generate form and fill
    form = Form.from_model(Category)
    form.fill(cat)

    # Should show the default value
    assert form.locale.value == "en"

    Category.close_connections()


def test_enum_in_select_field_choices():
    """Test that Enum values match select field choices."""
    form = Form.from_model(Category)

    # Get the locale field
    locale_field = form.locale

    # Check it's a select
    assert locale_field.type == "select"

    # Check choices are correct
    choice_values = [choice[0] for choice in locale_field.choices]
    assert "en" in choice_values
    assert "fr" in choice_values
    assert "de" in choice_values


def test_enum_form_render_with_value():
    """Test that Enum select renders with correct selected value."""
    Category.create_table()

    cat = Category.create(name="Test", locale=Language.FR)

    form = Form.from_model(Category)
    form.fill(cat)

    # Render the select input
    html = form.locale.render_input()

    # Should have the FR option selected
    assert 'value="fr"' in html
    assert "selected" in html
    # The selected attribute should be on the FR option
    assert 'value="fr" selected' in html or 'value="fr"  selected' in html

    Category.close_connections()


def test_color_default_value():
    """Test that Color default values work correctly."""
    Category.create_table()

    cat = Category(name="Test")
    assert cat.color == "#3b82f6"

    Category.close_connections()


def test_enum_nullable_allows_none():
    """Test that nullable Enum fields accept None."""
    Category.create_table()

    cat = Category.create(name="Test", locale=None)
    assert cat.locale is None

    cat_loaded = Category.find(id=cat.id)
    assert cat_loaded.locale is None

    Category.close_connections()


def test_enum_create_with_string_via_create():
    """Test creating model with Enum string via Model.create()."""
    Category.create_table()

    # Create with string value (simulating form data)
    cat = Category.create(name="Test", locale="de")

    # Should be converted to Enum
    assert cat.locale == Language.DE
    assert isinstance(cat.locale, Language)

    Category.close_connections()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
