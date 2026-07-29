"""Regression test for the admin AdminSession list refresh.

BUG: _sync_sessions_if_needed called `self.admin._fetch_all_raw_sessions()`, but
CRUDViewsMixin is mixed into Admin so `self` IS the admin (no `.admin`). The
AttributeError was swallowed by a bare `except`, so opening /admin/admin_sessions
never synced the live session store to the AdminSession table — data only
refreshed after visiting the separate /active-sessions view.
"""

from unittest.mock import MagicMock

import pytest

from asok import Asok
from asok.admin import Admin


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret"})
    return Admin(app)


def test_admin_session_list_triggers_store_sync(admin):
    """Listing the AdminSession model must refresh from the live store."""
    admin._fetch_all_raw_sessions = MagicMock()
    entry = {"slug": "admin_sessions", "model": type("AdminSession", (), {})}

    admin._sync_sessions_if_needed(entry)

    admin._fetch_all_raw_sessions.assert_called_once()


def test_active_sessions_slug_triggers_store_sync(admin):
    """The custom /active-sessions slug also triggers a sync."""
    admin._fetch_all_raw_sessions = MagicMock()
    entry = {"slug": "active-sessions", "model": type("Whatever", (), {})}

    admin._sync_sessions_if_needed(entry)

    admin._fetch_all_raw_sessions.assert_called_once()


def test_unrelated_model_does_not_sync(admin):
    """A normal model list must not trigger the session store sync."""
    admin._fetch_all_raw_sessions = MagicMock()
    entry = {"slug": "posts", "model": type("Post", (), {})}

    admin._sync_sessions_if_needed(entry)

    admin._fetch_all_raw_sessions.assert_not_called()
