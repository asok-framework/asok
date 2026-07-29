import time
import uuid
from unittest.mock import MagicMock

from asok.admin.core import Admin
from asok.admin.models import _DEFAULT_SESSION_MODEL_SRC
from asok.orm import MODELS_REGISTRY, Field, Model


def test_admin_session_model_source_contains_expected_fields():
    assert "class AdminSession(Model):" in _DEFAULT_SESSION_MODEL_SRC
    assert '__tablename__ = "asok_sessions"' in _DEFAULT_SESSION_MODEL_SRC
    assert "sid = Field.String" in _DEFAULT_SESSION_MODEL_SRC
    assert "user_id = Field.Integer" in _DEFAULT_SESSION_MODEL_SRC
    assert "is_active = Field.Boolean" in _DEFAULT_SESSION_MODEL_SRC


def test_admin_session_sync_and_revocation(monkeypatch):
    class MockAdminSession(Model):
        _table = "test_asok_sessions"
        sid = Field.String(unique=True, nullable=False)
        user_id = Field.Integer(nullable=True)
        ip = Field.String()
        user_agent = Field.String()
        device_type = Field.String()
        browser = Field.String()
        os_name = Field.String()
        location = Field.String()
        is_active = Field.Boolean(default=True)
        last_seen = Field.Integer()
        created_at = Field.CreatedAt()
        revoked_at = Field.Integer(nullable=True)

    MODELS_REGISTRY["AdminSession"] = MockAdminSession
    MockAdminSession.create_table()

    admin = Admin.__new__(Admin)
    admin.app = MagicMock(spec=[])

    now_ts = int(time.time())
    raw_sessions = [
        {
            "sid": "sess_abc123",
            "user_id": 42,
            "ip": "192.168.1.1",
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X)",
            "last_seen": now_ts,
            "created_at": now_ts - 300,
            "is_active": True,
        }
    ]

    admin._sync_sessions_to_db(raw_sessions)

    hit = MockAdminSession.find(sid="sess_abc123")
    assert hit is not None
    assert hit.user_id == 42
    assert bool(hit.is_active) is True

    # Revoke single session
    admin._revoke_db_session("sess_abc123")
    row_revoked = MockAdminSession.find(sid="sess_abc123")
    assert row_revoked is not None
    assert bool(row_revoked.is_active) is False
    assert row_revoked.revoked_at is not None

    # Revoke all user sessions
    raw_sessions2 = [
        {
            "sid": "sess_def456",
            "user_id": 99,
            "ip": "10.0.0.1",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0)",
            "last_seen": now_ts,
            "created_at": now_ts,
            "is_active": True,
        }
    ]
    admin._sync_sessions_to_db(raw_sessions2)
    admin._revoke_user_db_sessions(99)
    row_user_revoked = MockAdminSession.find(sid="sess_def456")
    assert row_user_revoked is not None
    assert bool(row_user_revoked.is_active) is False


def test_string_timestamp_session_building():
    now_ts = time.time()
    raw_session_str_ts = {
        "sid": "sess_str123",
        "user_id": 5,
        "created_at": "2026-07-22 22:54:19",
        "last_seen": "1784760859",
        "user_agent": "Mozilla/5.0",
        "ip": "127.0.0.1",
        "is_active": True,
    }
    mock_user = MagicMock()
    mock_user.email = "test@example.com"
    mock_user.is_admin = True

    sess_dict = Admin._build_session_dict(
        raw_session_str_ts, "5", mock_user, now_ts, now_ts
    )
    assert sess_dict["sid"] == "sess_str123"
    assert isinstance(sess_dict["created_at"], (int, float))
    assert "2026" in sess_dict["created_at_str"]


def test_deactivate_stale_db_sessions():
    class StaleAdminSession(Model):
        _table = "test_stale_sessions"
        sid = Field.String(unique=True, nullable=False)
        is_active = Field.Boolean(default=True)
        last_seen = Field.Integer()
        created_at = Field.CreatedAt()
        revoked_at = Field.Integer(nullable=True)

    MODELS_REGISTRY["AdminSession"] = StaleAdminSession
    StaleAdminSession.create_table()

    sid_active = f"active_{uuid.uuid4().hex}"
    sid_stale = f"stale_{uuid.uuid4().hex}"

    StaleAdminSession.create(_trust=True, sid=sid_active, is_active=True, last_seen=1000)
    StaleAdminSession.create(_trust=True, sid=sid_stale, is_active=True, last_seen=1000)

    admin = Admin.__new__(Admin)
    admin.app = MagicMock()
    admin._sync_sessions_to_db([{"sid": sid_active, "is_active": True}])

    fresh_s1 = StaleAdminSession.find(sid=sid_active)
    fresh_s2 = StaleAdminSession.find(sid=sid_stale)
    assert bool(fresh_s1.is_active) is True
    assert bool(fresh_s2.is_active) is False


def test_active_sessions_i18n_formatting():
    assert Admin._format_location("127.0.0.1", "en") == "Local Network (Localhost)"
    assert Admin._format_location("127.0.0.1", "fr") == "Réseau local (Localhost)"
    assert Admin._format_location("127.0.0.1", "es") == "Red local (Localhost)"

    assert Admin._format_last_seen(10, "en") == "just now"
    assert Admin._format_last_seen(10, "fr") == "à l'instant"
    assert Admin._format_last_seen(10, "es") == "ahora mismo"

    assert Admin._format_last_seen(300, "en") == "5 min ago"
    assert Admin._format_last_seen(300, "fr") == "il y a 5 min"
    assert Admin._format_last_seen(300, "es") == "hace 5 min"
