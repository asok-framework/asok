"""Regression: a model field named like a Form instance member (data, errors,
validate, ...) must not shadow that member.

Before the fix, Form.__getattribute__ returned the FormField for `form.data`,
so `Model.create(**form.data)` broke (form.data was a FormField, not the dict).
Common field names that are also Form schema helpers (email, text, ...) must
still resolve to the field.
"""

import pytest

from asok import Asok, Field, Model
from asok.forms import Form
from asok.orm import MODELS_REGISTRY


class Record(Model):
    __tablename__ = "records_collision"
    data = Field.String()
    errors = Field.String()
    email = Field.String()
    name = Field.String()


@pytest.fixture(autouse=True)
def _app():
    a = Asok()
    a.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["Record"] = Record
    return a


def test_reserved_members_win_over_fields():
    form = Form.from_model(Record)
    assert isinstance(form.data, dict)
    assert isinstance(form.errors, dict)
    assert callable(form.validate)
    # The `data` field's value stays reachable through the values dict.
    assert "data" in form.data


def test_common_field_names_still_resolve_to_field():
    form = Form.from_model(Record)
    # `email`/`name` are Form schema helpers on the class but must be the field
    # on an instance.
    assert type(form.email).__name__ == "FormField"
    assert type(form.name).__name__ == "FormField"
    assert form.email.name == "email"
