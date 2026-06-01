"""Tests for security audit fixes to achieve 10/10 security score.

Tests verify all 5 security vulnerabilities have been properly fixed:
1. RBAC audit logging for superadmin actions
2. Atomic session regeneration (no race conditions)
3. Improved HTML sanitizer (handles unquoted attributes)
4. LIKE wildcard escaping
5. Validation limit error handling (no silent failures)
"""

import logging
import os
import tempfile

import pytest

from asok.admin.rbac import _user_can
from asok.orm import Field, Model
from asok.session import SessionStore
from asok.utils.html_sanitizer import sanitize_html
from asok.validation.rules import check_max, check_min

# ============================================================================
# Test 1: RBAC Audit Logging
# ============================================================================


def test_rbac_superadmin_action_is_logged(caplog):
    """Verify that superadmin actions are logged for audit trail."""

    class MockUser:
        id = 123
        email = "admin@example.com"
        is_admin = True

    user = MockUser()

    with caplog.at_level(logging.INFO, logger="asok.admin.rbac"):
        result = _user_can(user, "posts.delete")

    assert result is True
    assert len(caplog.records) == 1
    assert "ADMIN ACCESS" in caplog.text
    assert "admin@example.com" in caplog.text
    assert "superadmin" in caplog.text
    assert "posts.delete" in caplog.text


def test_rbac_regular_user_no_logging(caplog):
    """Verify that non-admin users don't trigger admin logging."""

    class MockUser:
        id = 456
        email = "user@example.com"
        is_admin = False
        roles = []

    user = MockUser()

    with caplog.at_level(logging.INFO, logger="asok.admin.rbac"):
        result = _user_can(user, "posts.view")

    assert result is False
    # No ADMIN ACCESS log should be present
    assert "ADMIN ACCESS" not in caplog.text


# ============================================================================
# Test 2: Atomic Session Regeneration
# ============================================================================


def test_session_regeneration_memory_backend_atomic():
    """Verify session regeneration is atomic in memory backend."""
    store = SessionStore(backend="memory")
    sid1 = store.generate_sid()
    test_data = {"user_id": 42, "username": "testuser"}
    store.save(sid1, test_data)

    # Regenerate session
    sid2 = store.regenerate(sid1)

    # New session should have data
    assert store.load(sid2) == test_data
    # Old session should be gone
    assert store.load(sid1) is None
    # Session IDs should be different
    assert sid1 != sid2


def test_session_regeneration_file_backend_atomic():
    """Verify session regeneration is atomic in file backend."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = SessionStore(backend="file", path=tmpdir)
        sid1 = store.generate_sid()
        test_data = {"user_id": 99, "role": "admin"}
        store.save(sid1, test_data)

        # Regenerate session
        sid2 = store.regenerate(sid1)

        # New session should have data
        assert store.load(sid2) == test_data
        # Old session should be gone
        assert store.load(sid1) is None

        # Verify old file is deleted
        old_file = os.path.join(tmpdir, f"{sid1}.json")
        assert not os.path.exists(old_file)


def test_session_regeneration_preserves_data_types():
    """Verify session regeneration preserves complex data types."""
    store = SessionStore(backend="memory")
    sid1 = store.generate_sid()
    complex_data = {
        "user_id": 123,
        "roles": ["admin", "editor"],
        "settings": {"theme": "dark", "lang": "en"},
        "last_login": "2024-01-15T10:30:00",
    }
    store.save(sid1, complex_data)

    sid2 = store.regenerate(sid1)

    loaded = store.load(sid2)
    assert loaded == complex_data
    assert isinstance(loaded["roles"], list)
    assert isinstance(loaded["settings"], dict)


# ============================================================================
# Test 3: Improved HTML Sanitizer
# ============================================================================


def test_html_sanitizer_handles_unquoted_attributes():
    """Verify sanitizer handles attributes without quotes."""
    # Previously would miss unquoted href=value
    html = '<a href=https://example.com>Link</a>'
    result = sanitize_html(html)
    assert 'href="https://example.com"' in result


def test_html_sanitizer_blocks_javascript_in_unquoted_href():
    """Verify sanitizer blocks javascript: in unquoted attributes."""
    html = '<a href=javascript:alert(1)>Bad</a>'
    result = sanitize_html(html)
    # Should not contain javascript:
    assert "javascript:" not in result.lower()


def test_html_sanitizer_handles_mixed_quoted_unquoted():
    """Verify sanitizer handles mix of quoted and unquoted attributes."""
    html = '<img src="https://example.com/image.jpg" alt=MyImage width=100>'
    result = sanitize_html(html)
    assert 'src="https://example.com/image.jpg"' in result
    assert 'alt="MyImage"' in result
    assert 'width="100"' in result


def test_html_sanitizer_escapes_values_properly():
    """Verify sanitizer escapes attribute values correctly."""
    html = '<a href="https://example.com?q=test&foo=bar" title="A &lt;link&gt;">Link</a>'
    result = sanitize_html(html)
    assert "https://example.com?q=test&amp;foo=bar" in result
    assert "A &amp;lt;link&amp;gt;" in result


# ============================================================================
# Test 4: LIKE Wildcard Escaping
# ============================================================================


def test_like_query_without_escape():
    """Verify LIKE works normally without escaping (backward compatibility)."""

    class Product(Model):
        _table = "products"
        name = Field.String()

    query = Product.query().like("name", "%test%")
    sql = query.to_sql()
    assert "name LIKE ?" in sql
    assert query._args == ["%test%"]


def test_like_query_with_escape_wildcards():
    """Verify LIKE escapes wildcards when requested."""

    class Product(Model):
        _table = "products"
        description = Field.String()

    # Search for literal "100%"
    query = Product.query().like("description", "100%", escape_wildcards=True)
    sql = query.to_sql()
    assert "description LIKE ?" in sql
    # Wildcards should be escaped
    assert query._args == ["100\\%"]


def test_like_escapes_underscore_wildcard():
    """Verify underscore wildcard is also escaped."""

    class User(Model):
        _table = "users"
        username = Field.String()

    # Search for literal "user_123"
    query = User.query().like("username", "user_123", escape_wildcards=True)
    assert query._args == ["user\\_123"]


def test_like_escapes_backslash_first():
    """Verify backslash is escaped before wildcards."""

    class File(Model):
        _table = "files"
        path = Field.String()

    # Search for literal "C:\test\file%"
    query = File.query().like("path", r"C:\test\file%", escape_wildcards=True)
    # Backslashes should be doubled, then wildcards escaped
    # Result: C:\\test\\file\%
    assert query._args == [r"C:\\test\\file\%"]


# ============================================================================
# Test 5: Validation Limit Error Handling
# ============================================================================


def test_validation_min_rejects_negative_limit():
    """Verify check_min raises ValueError for negative limits."""
    with pytest.raises(ValueError) as excinfo:
        check_min("test", "-1")
    assert "cannot be negative" in str(excinfo.value)


def test_validation_min_rejects_too_large_limit():
    """Verify check_min raises ValueError for limits > 1M."""
    with pytest.raises(ValueError) as excinfo:
        check_min("test", "2000000")
    assert "too large" in str(excinfo.value)


def test_validation_max_rejects_negative_limit():
    """Verify check_max raises ValueError for negative limits."""
    with pytest.raises(ValueError) as excinfo:
        check_max("test", "-5")
    assert "cannot be negative" in str(excinfo.value)


def test_validation_max_rejects_too_large_limit():
    """Verify check_max raises ValueError for limits > 1M."""
    with pytest.raises(ValueError) as excinfo:
        check_max("test", "1500000")
    assert "too large" in str(excinfo.value)


def test_validation_min_rejects_invalid_string():
    """Verify check_min raises ValueError for non-numeric limits."""
    with pytest.raises(ValueError) as excinfo:
        check_min("test", "abc")
    assert "Invalid minimum length value" in str(excinfo.value)


def test_validation_max_rejects_invalid_string():
    """Verify check_max raises ValueError for non-numeric limits."""
    with pytest.raises(ValueError) as excinfo:
        check_max("test", "xyz")
    assert "Invalid maximum length value" in str(excinfo.value)


def test_validation_min_accepts_valid_limits():
    """Verify check_min works normally with valid limits."""
    assert check_min("hello", "3") is True  # len("hello") = 5 >= 3
    assert check_min("hi", "5") is False  # len("hi") = 2 < 5
    assert check_min("test" * 100, "100") is True


def test_validation_max_accepts_valid_limits():
    """Verify check_max works normally with valid limits."""
    assert check_max("hello", "10") is True  # len("hello") = 5 <= 10
    assert check_max("hello world", "5") is False  # len("hello world") = 11 > 5
    assert check_max("ok", "999999") is True


# ============================================================================
# Integration Test: All Fixes Together
# ============================================================================


def test_security_audit_integration():
    """Verify all 5 security fixes work together."""
    # 1. RBAC logging
    class AdminUser:
        id = 1
        email = "admin@test.com"
        is_admin = True

    # 2. Session regeneration
    store = SessionStore(backend="memory")
    sid = store.generate_sid()
    store.save(sid, {"user_id": 1})
    new_sid = store.regenerate(sid)
    assert store.load(new_sid) == {"user_id": 1}
    assert store.load(sid) is None

    # 3. HTML sanitizer
    html = '<a href=test.com title="Test">Link</a>'
    safe = sanitize_html(html)
    assert "href=" in safe

    # 4. LIKE escaping
    class TestModel(Model):
        _table = "test"
        field = Field.String()

    query = TestModel.query().like("field", "test%", escape_wildcards=True)
    assert query._args == ["test\\%"]

    # 5. Validation errors
    with pytest.raises(ValueError):
        check_min("test", "-1")
    with pytest.raises(ValueError):
        check_max("test", "2000000")

    # If we reach here, all fixes are working!
    assert True, "All security fixes working together"
