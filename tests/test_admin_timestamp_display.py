"""Regression: auto-managed timestamp fields (created_at, ...) are shown on the
admin detail view as read-only, instead of being skipped entirely.

BUG: _should_skip_field dropped every non-readonly timestamp from the form
schema, so AdminLog.created_at (and any created_at/updated_at) never appeared on
the detail view even though the record has the value.
"""

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.admin.forms import _populate_readonly_slugs
from asok.orm import MODELS_REGISTRY


class Widget(Model):
    __tablename__ = "widgets_ts"
    name = Field.String()
    created_at = Field.CreatedAt()


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret"})
    MODELS_REGISTRY["Widget"] = Widget
    return Admin(app)


def test_timestamp_is_marked_readonly(admin):
    readonly: set[str] = set()
    _populate_readonly_slugs(Widget, readonly)
    assert "created_at" in readonly


def test_detail_schema_includes_created_at(admin):
    readonly: set[str] = set()
    _populate_readonly_slugs(Widget, readonly)
    schema, meta = admin._build_schema_and_meta(Widget, set(), readonly, False)
    assert "created_at" in schema
    assert meta["created_at"]["is_datetime"] is True
