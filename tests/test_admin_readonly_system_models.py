"""Regression tests: AdminLog and AdminSession are read-only in the admin.

BUG: only log models were forced read-only (_is_log_model). AdminSession fell
through to the default, so can_add/can_edit/can_delete were all True — the
session records could be edited and deleted from the CRUD UI. They must be
read-only (managed internally + via the /active-sessions revoke flow).
"""

import types

import pytest

from asok import Asok
from asok.admin import Admin


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret"})
    return Admin(app)


@pytest.mark.parametrize("model_name", ["AdminSession", "AdminLog"])
def test_system_model_is_readonly(admin, model_name):
    assert admin._resolve_can_add(None, model_name) is False
    assert admin._resolve_can_edit(None, model_name) is False
    assert admin._resolve_can_delete(None, model_name) is False


def test_normal_model_stays_writable(admin):
    assert admin._resolve_can_add(None, "Post") is True
    assert admin._resolve_can_edit(None, "Post") is True
    assert admin._resolve_can_delete(None, "Post") is True


def test_explicit_option_can_override_readonly(admin):
    """An explicit can_delete=True on the admin config still wins (opt-in escape)."""
    opts = types.SimpleNamespace(can_delete=True)
    assert admin._resolve_can_delete(opts, "AdminSession") is True
