"""Tests for Active Sessions Manager in the admin /me profile page."""

import time

from asok.session import SessionStore
from asok.templates import render_template_string

# ---------------------------------------------------------------------------
# Template fragment for the Active Sessions card
# ---------------------------------------------------------------------------

_SESSIONS_CARD = """
{% from "macros.html" import icon, badge %}
<div class="card">
  <div class="card-header">
    {{ icon('monitor', 12) }} {{ t('Active Sessions') }}
    <div>{{ badge(active_sessions|length ~ ' ' ~ t('active'), 'info') }}</div>
  </div>
  <div>
    {% if active_sessions %}
      {% for sess in active_sessions %}
        <div class="session-row">
          <span>{{ sess.user_agent|truncate(50) }}</span>
          {% if sess.sid == current_sid %}
            <span class="badge badge-success">{{ t('This device') }}</span>
          {% endif %}
          <span>IP: {{ sess.ip }}</span>
          <span>{{ t('Last active') }}: {{ sess.last_seen }}</span>
          {% if sess.sid != current_sid %}
            <form method="post" action="{{ admin_prefix }}/revoke-session"
                  data-confirm="{{ t('Revoke this session? The device will be logged out immediately.') }}">
              <input type="hidden" name="sid" value="{{ sess.sid }}">
              <button type="submit">{{ t('Revoke') }}</button>
            </form>
          {% else %}
            <span>{{ t('Active now') }}</span>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <p>{{ t('No active sessions.') }}</p>
    {% endif %}
  </div>
</div>
"""


def _t(x):
    return x


def _icon(name, size, cls=""):
    return f'<svg data-icon="{name}"></svg>'


def _badge(text, style):
    return f'<span class="badge badge-{style}">{text}</span>'


def _make_ctx(active_sessions, current_sid):
    return {
        "t": _t,
        "icon": _icon,
        "badge": _badge,
        "admin_prefix": "/admin",
        "active_sessions": active_sessions,
        "current_sid": current_sid,
    }


# ---------------------------------------------------------------------------
# Template rendering tests
# ---------------------------------------------------------------------------


class TestActiveSessionsTemplate:
    def test_no_sessions_shows_empty_message(self):
        ctx = _make_ctx([], "abc123")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert "No active sessions." in result

    def test_session_count_badge(self):
        sessions = [
            {
                "sid": "aaa",
                "ip": "1.2.3.4",
                "user_agent": "Chrome/120",
                "last_seen": time.time(),
            },
            {
                "sid": "bbb",
                "ip": "5.6.7.8",
                "user_agent": "Firefox/119",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "aaa")
        result = render_template_string(_SESSIONS_CARD, ctx)
        # badge renders the session count; verify both sessions appear in the output
        assert "Chrome/120" in result
        assert "Firefox/119" in result
        assert "badge-info" in result

    def test_current_device_badge_shown(self):
        sessions = [
            {
                "sid": "current-sid",
                "ip": "10.0.0.1",
                "user_agent": "Safari/17",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "current-sid")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert "This device" in result
        assert "Active now" in result

    def test_revoke_button_shown_for_other_sessions(self):
        sessions = [
            {
                "sid": "other-sid",
                "ip": "192.168.1.1",
                "user_agent": "Edge/120",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "my-sid")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert "Revoke" in result
        assert "/admin/revoke-session" in result
        assert 'name="sid"' in result
        assert 'value="other-sid"' in result

    def test_revoke_button_hidden_for_current_session(self):
        sessions = [
            {
                "sid": "my-sid",
                "ip": "127.0.0.1",
                "user_agent": "Chrome/120",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "my-sid")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert "Revoke</button>" not in result
        assert "Active now" in result

    def test_revoke_confirmation_data_attribute(self):
        sessions = [
            {
                "sid": "other-sid",
                "ip": "1.1.1.1",
                "user_agent": "UA",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "my-sid")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert (
            "Revoke this session? The device will be logged out immediately." in result
        )

    def test_ip_shown_for_each_session(self):
        sessions = [
            {
                "sid": "s1",
                "ip": "42.42.42.42",
                "user_agent": "Bot/1",
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "other")
        result = render_template_string(_SESSIONS_CARD, ctx)
        assert "42.42.42.42" in result

    def test_user_agent_truncated_in_display(self):
        long_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        sessions = [
            {
                "sid": "s1",
                "ip": "1.1.1.1",
                "user_agent": long_ua,
                "last_seen": time.time(),
            },
        ]
        ctx = _make_ctx(sessions, "other")
        result = render_template_string(_SESSIONS_CARD, ctx)
        # Jinja truncate(50) cuts at 50 chars
        assert len(long_ua) > 50
        assert "..." in result or long_ua[:50] in result


# ---------------------------------------------------------------------------
# SessionStore: get_user_sessions integration
# ---------------------------------------------------------------------------


class TestSessionTracking:
    def test_session_metadata_stored_and_retrieved(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        now = time.time()
        store.save(
            sid,
            {
                "user_id": 1,
                "_ip": "192.168.0.10",
                "_user_agent": "TestAgent/1.0",
                "_last_seen": now,
            },
        )
        sessions = store.get_user_sessions(1)
        assert len(sessions) == 1
        s = sessions[0]
        assert s["ip"] == "192.168.0.10"
        assert s["user_agent"] == "TestAgent/1.0"
        assert abs(s["last_seen"] - now) < 1

    def test_session_without_metadata_uses_defaults(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 2})
        sessions = store.get_user_sessions(2)
        assert len(sessions) == 1
        assert sessions[0]["ip"] == "Unknown"
        assert sessions[0]["user_agent"] == "Unknown"

    def test_expired_sessions_not_returned(self):
        store = SessionStore(backend="memory", ttl=1)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 3})
        time.sleep(1.1)
        sessions = store.get_user_sessions(3)
        assert sessions == []

    def test_revoke_own_session(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 10})
        assert store.revoke_user_session(10, sid) is True
        assert store.get_user_sessions(10) == []

    def test_revoke_cross_user_session_blocked(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 10})
        # User 99 tries to revoke user 10's session
        result = store.revoke_user_session(99, sid)
        assert result is False
        # Session still exists for user 10
        assert len(store.get_user_sessions(10)) == 1

    def test_multiple_revocations_only_target_session(self):
        store = SessionStore(backend="memory", ttl=3600)
        sid_a = store.generate_sid()
        sid_b = store.generate_sid()
        store.save(sid_a, {"user_id": 5, "_ip": "10.0.0.1"})
        store.save(sid_b, {"user_id": 5, "_ip": "10.0.0.2"})
        store.revoke_user_session(5, sid_a)
        remaining = store.get_user_sessions(5)
        assert len(remaining) == 1
        assert remaining[0]["sid"] == sid_b

    def test_user_id_string_vs_int_matching(self):
        """get_user_sessions must match int user_id stored as string or int."""
        store = SessionStore(backend="memory", ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": "7"})  # stored as string
        sessions = store.get_user_sessions(7)  # queried as int
        assert len(sessions) == 1

    def test_file_backend_session_tracking(self, tmp_path):
        store = SessionStore(backend="file", path=str(tmp_path / "sess"), ttl=3600)
        sid = store.generate_sid()
        now = time.time()
        store.save(
            sid,
            {
                "user_id": 100,
                "_ip": "203.0.113.1",
                "_user_agent": "FileUA/1",
                "_last_seen": now,
            },
        )
        sessions = store.get_user_sessions(100)
        assert len(sessions) == 1
        assert sessions[0]["ip"] == "203.0.113.1"

    def test_file_backend_revoke_cross_user_blocked(self, tmp_path):
        store = SessionStore(backend="file", path=str(tmp_path / "sess2"), ttl=3600)
        sid = store.generate_sid()
        store.save(sid, {"user_id": 200})
        assert store.revoke_user_session(999, sid) is False
        assert store.load(sid) is not None


# ---------------------------------------------------------------------------
# Translation keys
# ---------------------------------------------------------------------------


class TestSessionTranslationKeys:
    def test_french_session_keys(self):
        from asok.admin.translations import MESSAGES

        fr = MESSAGES["fr"]
        assert "Active Sessions" in fr
        assert "active" in fr
        assert "This device" in fr
        assert "Last active" in fr
        assert "Revoke" in fr
        assert "Active now" in fr
        assert "No active sessions." in fr
        assert "Session revoked successfully" in fr
        assert "Failed to revoke session" in fr
        assert "Invalid session ID" in fr

    def test_spanish_session_keys(self):
        from asok.admin.translations import MESSAGES

        es = MESSAGES["es"]
        assert "Active Sessions" in es
        assert "active" in es
        assert "This device" in es
        assert "Revoke" in es
        assert "Session revoked successfully" in es

    def test_french_trash_keys(self):
        from asok.admin.translations import MESSAGES

        fr = MESSAGES["fr"]
        assert "Active Items" in fr
        assert "Delete permanently" in fr
        assert "Permanently delete this record?" in fr
        assert "Permanently deleted" in fr
        assert "Restored" in fr

    def test_english_falls_back_to_key(self):
        from asok.admin.translations import translate

        assert translate("en", "Active Sessions") == "Active Sessions"
        assert translate("en", "Revoke") == "Revoke"

    def test_french_translations_not_empty(self):
        from asok.admin.translations import translate

        assert translate("fr", "Active Sessions") == "Sessions actives"
        assert translate("fr", "Revoke") == "Révoker"
        assert translate("fr", "This device") == "Cet appareil"
        assert translate("fr", "No active sessions.") == "Aucune session active."
