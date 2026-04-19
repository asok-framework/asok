"""
Tests for the mail module.
Covers: send (sync/async), HTML alternatives, CC/BCC, sender resolution,
and header injection sanitization.
"""

import pytest

from asok.mail import Mail

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_do_send(monkeypatch):
    """Intercept the actual SMTP dispatch to inspect arguments instead of sending."""
    sent_emails = []

    def fake_do_send(
        sender, all_recipients, msg_string, host, port, username, password, use_tls
    ):
        sent_emails.append(
            {
                "sender": sender,
                "recipients": all_recipients,
                "msg": msg_string,
            }
        )

    monkeypatch.setattr(Mail, "_do_send", fake_do_send)

    # Also patch configuration for predictable tests
    def fake_cfg(key, default=None):
        cfgs = {
            "MAIL_HOST": "smtp.test",
            "MAIL_PORT": "2525",
            "MAIL_FROM": "default@test.com",
        }
        return cfgs.get(key, default)

    monkeypatch.setattr(Mail, "_cfg", fake_cfg)

    return sent_emails


# ---------------------------------------------------------------------------
# Core sending logic
# ---------------------------------------------------------------------------


class TestMailDispatch:
    def test_send_sync(self, mock_do_send):
        result = Mail.send(
            to="alice@example.com", subject="Hello", body="World", sync=True
        )
        assert result is None  # Sync returns None
        assert len(mock_do_send) == 1
        assert "alice@example.com" in mock_do_send[0]["recipients"]
        assert "Hello" in mock_do_send[0]["msg"]

    def test_send_async_returns_thread(self, mock_do_send):
        Mail.send(to="alice@example.com", subject="Async", body="World")
        # Mail.send doesn't return the thread, it just fires it.
        # It's None regardless of sync mode.
        import time

        time.sleep(0.05)  # Wait for background thread to dispatch
        assert len(mock_do_send) == 1

    def test_default_sender_used_if_not_provided(self, mock_do_send):
        Mail.send(to="a@test.com", subject="T", body="B", sync=True)
        assert mock_do_send[0]["sender"] == "default@test.com"

    def test_custom_sender(self, mock_do_send):
        Mail.send(
            to="a@test.com",
            subject="T",
            body="B",
            from_addr="custom@test.com",
            sync=True,
        )
        assert mock_do_send[0]["sender"] == "custom@test.com"


# ---------------------------------------------------------------------------
# Formatting and Recipients
# ---------------------------------------------------------------------------


class TestMailFormatting:
    def test_multiple_recipients(self, mock_do_send):
        Mail.send(to=["a@test.com", "b@test.com"], subject="T", body="B", sync=True)
        assert mock_do_send[0]["recipients"] == ["a@test.com", "b@test.com"]

    def test_cc_and_bcc(self, mock_do_send):
        Mail.send(
            to="a@test.com",
            subject="T",
            body="B",
            cc="cc@test.com",
            bcc=["bcc1@test.com", "bcc2@test.com"],
            sync=True,
        )
        recs = mock_do_send[0]["recipients"]
        assert "a@test.com" in recs
        assert "cc@test.com" in recs
        assert "bcc1@test.com" in recs
        assert "bcc2@test.com" in recs

        # BCC should NOT appear in the headers, but CC should
        msg = mock_do_send[0]["msg"]
        assert "Cc: cc@test.com" in msg
        assert "bcc1" not in msg

    def test_html_alternative(self, mock_do_send):
        Mail.send(
            to="a@test.com",
            subject="T",
            body="Plain Text",
            html="<h1>Rich Text</h1>",
            sync=True,
        )
        msg = mock_do_send[0]["msg"]
        assert "multipart/alternative" in msg


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class TestMailSecurity:
    def test_header_injection_sanitization(self, mock_do_send):
        """Line breaks in subject or emails must be stripped to prevent injection."""
        Mail.send(
            to="a@test.com\nBcc: hacker@test.com",
            subject="Hello\r\nInject: Yes",
            body="Body",
            sync=True,
        )
        msg = mock_do_send[0]["msg"]
        assert "hacker@test.com" not in mock_do_send[0]["recipients"]
        # The newlines should have been stripped by _sanitize
        assert "HelloInject: Yes" in msg
