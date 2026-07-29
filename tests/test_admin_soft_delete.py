"""Tests for soft-delete (Trash) management in the admin interface."""

from asok.session import SessionStore
from asok.templates import render_template_string

# ---------------------------------------------------------------------------
# Template rendering helpers
# ---------------------------------------------------------------------------

_LIST_TRASH_HEADER = """
{% from "macros.html" import icon, btn %}
<div class="page-actions">
  {% if has_soft_delete and not trash and user_can_delete %}
    <a href="{{ admin_prefix }}/{{ slug }}/trash" class="btn btn-ghost btn-sm">
      {{ icon('trash', 14) }} {{ t('Trash') }}
    </a>
  {% elif trash %}
    <a href="{{ admin_prefix }}/{{ slug }}" class="btn btn-ghost btn-sm">
      {{ icon('arrow-left', 14) }} {{ t('Active Items') }}
    </a>
  {% endif %}
  {% if can_add and user_can_add and not trash %}
    <a href="{{ admin_prefix }}/{{ slug }}/new" class="btn btn-primary btn-sm">
      {{ t('New') }}
    </a>
  {% endif %}
</div>
"""

_LIST_ROW_ACTIONS_TRASH = """
{% from "macros.html" import icon %}
{% if trash %}
  <form method="post" action="/admin/{{ slug }}/{{ row_id }}/restore">
    <button type="submit" title="{{ t('Restore') }}">{{ icon('restore', 16) }}</button>
  </form>
  {% if user_can_delete %}
  <form method="post" action="/admin/{{ slug }}/{{ row_id }}/force-delete"
        data-confirm="{{ t('Permanently delete this record?') }}">
    <button type="submit" title="{{ t('Delete permanently') }}">{{ icon('trash', 16) }}</button>
  </form>
  {% endif %}
{% else %}
  {% if can_delete and user_can_delete %}
  <form method="post" action="/admin/{{ slug }}/{{ row_id }}/delete"
        data-confirm="{{ t('Delete this record?') }}">
    <button type="submit" title="{{ t('Delete') }}">{{ icon('trash', 16) }}</button>
  </form>
  {% endif %}
{% endif %}
"""

def _t(x):
    return x


def _icon(name, size, cls=""):
    return f'<svg data-icon="{name}"></svg>'


def _ctx(**extra):
    return {"t": _t, "icon": _icon, "admin_prefix": "/admin", "slug": "posts", **extra}


# ---------------------------------------------------------------------------
# Trash header tests
# ---------------------------------------------------------------------------


class TestTrashHeader:
    def test_trash_button_shown_for_soft_delete_model(self):
        ctx = _ctx(has_soft_delete=True, trash=False, user_can_delete=True,
                   can_add=True, user_can_add=True, row_id=1)
        result = render_template_string(_LIST_TRASH_HEADER, ctx)
        assert "Trash" in result
        assert "/admin/posts/trash" in result

    def test_back_to_active_shown_in_trash_mode(self):
        ctx = _ctx(has_soft_delete=True, trash=True, user_can_delete=True,
                   can_add=True, user_can_add=True, row_id=1)
        result = render_template_string(_LIST_TRASH_HEADER, ctx)
        assert "Active Items" in result
        assert "/admin/posts" in result
        assert "Trash" not in result or "Active Items" in result

    def test_new_button_hidden_in_trash_mode(self):
        ctx = _ctx(has_soft_delete=True, trash=True, user_can_delete=True,
                   can_add=True, user_can_add=True, row_id=1)
        result = render_template_string(_LIST_TRASH_HEADER, ctx)
        assert "/admin/posts/new" not in result

    def test_trash_button_hidden_without_delete_permission(self):
        ctx = _ctx(has_soft_delete=True, trash=False, user_can_delete=False,
                   can_add=True, user_can_add=True, row_id=1)
        result = render_template_string(_LIST_TRASH_HEADER, ctx)
        assert "/admin/posts/trash" not in result

    def test_trash_button_hidden_for_hard_delete_model(self):
        ctx = _ctx(has_soft_delete=False, trash=False, user_can_delete=True,
                   can_add=True, user_can_add=True, row_id=1)
        result = render_template_string(_LIST_TRASH_HEADER, ctx)
        assert "/admin/posts/trash" not in result


# ---------------------------------------------------------------------------
# Row actions tests
# ---------------------------------------------------------------------------


class TestTrashRowActions:
    def test_restore_and_force_delete_shown_in_trash_mode(self):
        ctx = _ctx(trash=True, user_can_delete=True, can_delete=True, row_id=42)
        result = render_template_string(_LIST_ROW_ACTIONS_TRASH, ctx)
        assert "/admin/posts/42/restore" in result
        assert "/admin/posts/42/force-delete" in result
        assert "Permanently delete this record?" in result

    def test_restore_shown_without_delete_permission(self):
        ctx = _ctx(trash=True, user_can_delete=False, can_delete=True, row_id=7)
        result = render_template_string(_LIST_ROW_ACTIONS_TRASH, ctx)
        assert "/admin/posts/7/restore" in result
        assert "/admin/posts/7/force-delete" not in result

    def test_normal_delete_shown_outside_trash(self):
        ctx = _ctx(trash=False, user_can_delete=True, can_delete=True, row_id=5)
        result = render_template_string(_LIST_ROW_ACTIONS_TRASH, ctx)
        assert "/admin/posts/5/delete" in result
        assert "restore" not in result
        assert "force-delete" not in result

    def test_delete_hidden_without_permission_outside_trash(self):
        ctx = _ctx(trash=False, user_can_delete=False, can_delete=True, row_id=3)
        result = render_template_string(_LIST_ROW_ACTIONS_TRASH, ctx)
        assert "/admin/posts/3/delete" not in result


# ---------------------------------------------------------------------------
# SessionStore: get_user_sessions / revoke_user_session
# ---------------------------------------------------------------------------


class TestSessionStoreUserSessions:
    def test_get_user_sessions_empty_for_unknown_user(self):
        store = SessionStore(backend="memory", ttl=3600)
        assert store.get_user_sessions(99) == []

    def test_get_user_sessions_none_user_id_returns_empty(self):
        store = SessionStore(backend="memory", ttl=3600)
        assert store.get_user_sessions(None) == []

    def test_get_user_sessions_returns_matching_sessions(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 1, "_ip": "127.0.0.1", "_user_agent": "TestBrowser/1.0"})
        sessions = store.get_user_sessions(1)
        assert len(sessions) == 1
        assert sessions[0]["sid"] == sid
        assert sessions[0]["ip"] == "127.0.0.1"
        assert sessions[0]["user_agent"] == "TestBrowser/1.0"

    def test_get_user_sessions_filters_other_users(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid1 = store.generate_sid()
        sid2 = store.generate_sid()
        store.save(sid1, {"user_id": 1})
        store.save(sid2, {"user_id": 2})
        sessions = store.get_user_sessions(1)
        sids = [s["sid"] for s in sessions]
        assert sid1 in sids
        assert sid2 not in sids

    def test_get_user_sessions_multiple_sessions_same_user(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid_a = store.generate_sid()
        sid_b = store.generate_sid()
        store.save(sid_a, {"user_id": 5, "_ip": "1.2.3.4"})
        store.save(sid_b, {"user_id": 5, "_ip": "5.6.7.8"})
        sessions = store.get_user_sessions(5)
        assert len(sessions) == 2
        ips = {s["ip"] for s in sessions}
        assert ips == {"1.2.3.4", "5.6.7.8"}

    def test_revoke_user_session_removes_correct_session(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 3})
        result = store.revoke_user_session(3, sid)
        assert result is True
        assert store.load(sid) is None

    def test_revoke_user_session_rejects_wrong_user(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 3})
        result = store.revoke_user_session(99, sid)
        assert result is False
        assert store.load(sid) is not None

    def test_revoke_nonexistent_session_returns_false(self):
        store = SessionStore(backend="memory", ttl=3600)
        result = store.revoke_user_session(1, "aabbccddeeff" * 5)
        assert result is False

    def test_get_user_sessions_file_backend(self, tmp_path):
        store = SessionStore(backend="file", path=str(tmp_path / "sess"), ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 10, "_ip": "10.0.0.1", "_user_agent": "UA/2"})
        sessions = store.get_user_sessions(10)
        assert len(sessions) == 1
        assert sessions[0]["ip"] == "10.0.0.1"

    def test_revoke_user_session_file_backend(self, tmp_path):
        store = SessionStore(backend="file", path=str(tmp_path / "sess2"), ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 20})
        assert store.revoke_user_session(20, sid) is True
        assert store.load(sid) is None

    def test_get_user_sessions_sorted_by_last_seen(self):
        import time
        store = SessionStore(backend="memory", ttl=3600)
        sid_old = store.generate_sid()
        sid_new = store.generate_sid()
        store.save(sid_old, {"user_id": 7, "_last_seen": time.time() - 100})
        store.save(sid_new, {"user_id": 7, "_last_seen": time.time()})
        sessions = store.get_user_sessions(7)
        assert sessions[0]["sid"] == sid_new
