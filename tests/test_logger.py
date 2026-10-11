"""
Tests for the logger module.
Covers: get_logger (level, JSON formatting), RequestLogger middleware (timing, sanitization).
"""

import io
import json
import logging

import pytest

from asok.logger import (
    JSONFormatter,
    RequestLogger,
    configure_security_logger,
    get_logger,
)
from asok.request import Request

# ---------------------------------------------------------------------------
# get_logger and formatters
# ---------------------------------------------------------------------------


class TestGetLogger:
    def test_creates_logger_with_correct_name(self):
        log = get_logger("my_test_logger")
        assert isinstance(log, logging.Logger)
        assert log.name == "my_test_logger"

    def test_respects_custom_level(self):
        log = get_logger("lvl_logger", level="DEBUG")
        assert log.level == logging.DEBUG

    def test_json_formatter(self, monkeypatch):
        # Patch stream to intercept output
        log = get_logger("json_logger", json_format=True)
        # Find the console handler
        handler = log.handlers[0]
        stream = io.StringIO()
        handler.stream = stream

        log.info("Hello JSON")
        output = stream.getvalue()

        # It should be valid JSON
        data = json.loads(output)
        assert data["logger"] == "json_logger"
        assert data["level"] == "INFO"
        assert data["message"] == "Hello JSON"
        assert "timestamp" in data

    def test_json_formatter_serializes_arbitrary_extras(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="asok.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=20,
            msg="User login successful",
            args=(),
            exc_info=None,
        )
        record.user_id = "user_123"
        record.ip = "127.0.0.1"
        record.auth_method = "magic_link"

        output = formatter.format(record)
        data = json.loads(output)
        assert data["user_id"] == "user_123"
        assert data["ip"] == "127.0.0.1"
        assert data["auth_method"] == "magic_link"
        assert data["logger"] == "asok.test"

    def test_configure_security_logger(self):
        stream = io.StringIO()
        sec_logger = configure_security_logger(
            json_format=True, level="WARNING", stream=stream
        )
        assert sec_logger.name == "asok.security"
        assert sec_logger.level == logging.WARNING

        sec_logger.warning(
            "Rate limit reached",
            extra={"event": "rate_limit", "key": "ip:1.2.3.4", "count": 10},
        )
        output = stream.getvalue()
        data = json.loads(output)
        assert data["logger"] == "asok.security"
        assert data["level"] == "WARNING"
        assert data["event"] == "rate_limit"
        assert data["key"] == "ip:1.2.3.4"
        assert data["count"] == 10

    def test_reconfigure_logger(self):
        log = get_logger("reconf_logger", level="INFO")
        assert log.level == logging.INFO
        # Reconfigure to DEBUG
        log2 = get_logger("reconf_logger", level="DEBUG", reconfigure=True)
        assert log2.level == logging.DEBUG


# ---------------------------------------------------------------------------
# RequestLogger Middleware
# ---------------------------------------------------------------------------


class TestRequestLogger:
    @pytest.fixture
    def mock_request(self):
        env = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/users",
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "wsgi.input": io.BytesIO(b""),
            "wsgi.errors": io.BytesIO(),
        }
        return Request(env)

    def test_logs_request_and_duration(self, mock_request, monkeypatch):
        # Intercept the actual log call
        logged_msgs = []

        class FakeLogger:
            def info(self, msg, *args, **kwargs):
                logged_msgs.append(msg % args)

            def error(self, msg, *args, **kwargs):
                logged_msgs.append(msg % args)

        middleware = RequestLogger()
        middleware.logger = FakeLogger()

        # Dummy next handler returning a tuple (or string) like the real app
        def fake_next(req):
            return "Response"

        middleware(mock_request, fake_next)

        assert len(logged_msgs) == 1
        assert "GET" in logged_msgs[0]
        assert "/api/users" in logged_msgs[0]
        assert "ms" in logged_msgs[0]

    def test_sanitizes_crlf_in_logs(self, mock_request, monkeypatch):
        """CRLF injection in the path must be neutralized."""
        mock_request.path = "/api\n/users\r/hack"

        logged_msgs = []

        class FakeLogger:
            def info(self, msg, *args, **kwargs):
                logged_msgs.append(msg % args)

            def error(self, msg, *args, **kwargs):
                logged_msgs.append(msg % args)

        middleware = RequestLogger()
        middleware.logger = FakeLogger()

        def fake_next(req):
            return "Response"

        middleware(mock_request, fake_next)

        assert "\n" not in logged_msgs[0]
        assert "\r" not in logged_msgs[0]
        assert "/api/users/hack" in logged_msgs[0]
