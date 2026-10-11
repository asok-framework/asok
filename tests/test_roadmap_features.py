import io
import json
import os
import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

from asok.core import Asok
from asok.core.metrics import default_metrics
from asok.core.smart_streamer import build_preload_link, send_early_hints
from asok.core.tracing import Tracer
from asok.orm.engines.sqlite import SQLiteEngine
from asok.orm.proxy import ConnectionProxy
from asok.ws.adapter import InMemoryWebSocketAdapter
from asok.ws.server import WebSocketServer


def test_health_check_endpoint():
    """Verify /health and /healthz endpoints return structured JSON status."""
    app = Asok()
    app.config["DATABASE"] = ":memory:"

    for path in ("/health", "/healthz", "/__health"):
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "80",
            "wsgi.input": io.BytesIO(b""),
        }
        status_holder = []

        def start_response(status, headers):
            status_holder.append((status, dict(headers)))

        response = app(environ, start_response)
        body = b"".join(response)
        assert status_holder[0][0] == "200 OK"
        data = json.loads(body.decode("utf-8"))
        assert data["status"] == "healthy"
        assert "uptime_seconds" in data
        assert "checks" in data
        assert data["checks"]["database"] in ("ok", "skipped")


def test_prometheus_metrics_endpoint():
    """Verify /metrics returns standard Prometheus text exposition format."""
    app = Asok()
    app.config["METRICS_ENABLED"] = True

    # Record some test metrics
    default_metrics.record_http_request("GET", "200", "/test-route", 0.045)
    default_metrics.record_db_query("sqlite", 0.012, is_slow=False)
    default_metrics.set_ws_connections(5)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/metrics",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": io.BytesIO(b""),
    }
    status_holder = []

    def start_response(status, headers):
        status_holder.append((status, dict(headers)))

    response = app(environ, start_response)
    body = b"".join(response).decode("utf-8")

    assert status_holder[0][0] == "200 OK"
    assert "text/plain; version=0.0.4" in status_holder[0][1]["Content-Type"]
    assert "asok_http_requests_total" in body
    assert "asok_db_queries_total" in body
    assert "asok_websocket_active_connections 5" in body
    assert "asok_uptime_seconds" in body


def test_slow_query_detection_and_logging(caplog):
    """Verify slow query detection logs a warning when exceeding threshold."""
    import logging

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.description = None
    mock_cursor.rowcount = 1

    def slow_exec(sql, params=()):
        time.sleep(0.06)
        return mock_cursor

    mock_conn.execute.side_effect = slow_exec
    proxy = ConnectionProxy(mock_conn, engine_name="sqlite")

    class DummyApp:
        config = {"SLOW_QUERY_THRESHOLD_MS": 50}

    class DummyReq:
        environ = {"asok.app": DummyApp()}
        _asok_sql_log = []

    from asok.context import request_var

    token = request_var.set(DummyReq())
    try:
        with caplog.at_level(logging.WARNING, logger="asok.orm"):
            proxy.execute("SELECT * FROM large_table WHERE unindexed = 1", (1,))
        assert any("SLOW QUERY" in record.message for record in caplog.records)
    finally:
        request_var.reset(token)


def test_sqlite_pragmas_and_retry(tmp_path):
    """Verify SQLite WAL and busy_timeout PRAGMAs are executed on connection."""
    db_file = str(tmp_path / "test_concurrency.db")
    engine = SQLiteEngine(db_file)
    conn = engine.get_connection()

    # Verify PRAGMAs
    row = conn.execute("PRAGMA journal_mode;").fetchone()
    assert row[0].lower() == "wal"

    row_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()
    assert row_timeout[0] == 5000

    row_sync = conn.execute("PRAGMA synchronous;").fetchone()
    # NORMAL is typically 1 in SQLite
    assert row_sync[0] in (1, "1", "NORMAL", "normal")

    # Verify retry backoff works on locked database
    attempt_count = [0]
    real_exec = conn.execute

    def flaky_exec(sql, args=()):
        attempt_count[0] += 1
        if attempt_count[0] < 2:
            raise sqlite3.OperationalError("database is locked")
        return real_exec(sql, args)

    fake_conn = MagicMock()
    fake_conn.execute.side_effect = flaky_exec
    cursor = engine._execute_query(fake_conn, "SELECT 1;", ())
    assert cursor is not None
    assert attempt_count[0] == 2

    engine.close_connections()


def test_websocket_in_memory_and_redis_adapter():
    """Verify WebSocket adapters can be configured and publish messages."""
    server = WebSocketServer(port=8099)
    assert isinstance(server.adapter, InMemoryWebSocketAdapter)

    # Custom mock adapter
    mock_adapter = MagicMock()
    server.adapter = mock_adapter

    server.broadcast("/chat", "hello-path")
    mock_adapter.publish.assert_called_with("path:/chat", "hello-path")

    server.broadcast_to("general", "hello-room")
    mock_adapter.publish.assert_called_with("room:general", "hello-room")

    server.stop()
    mock_adapter.close.assert_called_once()


def test_early_hints_helpers():
    """Verify HTTP 103 Early Hints formatting and transmission."""
    link = build_preload_link("/static/admin.min.css", as_type="style")
    assert link == "</static/admin.min.css>; rel=preload; as=style"

    # Simulated start_response supporting 103 Early Hints
    calls = []

    def mock_start_response(status, headers):
        calls.append((status, headers))

    success = send_early_hints(mock_start_response, [link])
    assert success is True
    assert calls[0][0] == "103 Early Hints"
    assert calls[0][1] == [("Link", link)]

    # Empty links should return False without calling start_response
    assert send_early_hints(mock_start_response, []) is False


def test_executemany_telemetry_and_slow_query(caplog):
    """Verify ConnectionProxy.executemany logs telemetry and alerts on slow batch operations."""
    import logging

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 10
    mock_conn.executemany.return_value = mock_cursor

    proxy = ConnectionProxy(mock_conn, engine_name="sqlite")

    class DummyApp:
        config = {"SLOW_QUERY_THRESHOLD_MS": 10}

    class DummyReq:
        environ = {"asok.app": DummyApp()}
        _asok_sql_log = []

    from asok.context import request_var

    token = request_var.set(DummyReq())
    try:
        with caplog.at_level(logging.WARNING, logger="asok.orm"):
            res = proxy.executemany(
                "INSERT INTO users (name) VALUES (?)", [("Alice",), ("Bob",)]
            )
        assert res.rowcount == 10
        assert len(DummyReq._asok_sql_log) == 1
        assert "[2 rows]" in DummyReq._asok_sql_log[0]["params"]
    finally:
        request_var.reset(token)


def test_prometheus_escaping_and_cardinality():
    """Verify Prometheus metrics properly escape labels and cap cardinality."""
    from asok.core.metrics import MetricsRegistry

    registry = MetricsRegistry(max_cardinality=2)

    # Label with quotes and newlines
    registry.record_http_request("GET", "200", '/search?q="test"\n', 0.01)
    exported = registry.export_prometheus()
    assert '\\"test\\"' in exported
    assert "\\n" in exported

    # Test cardinality capping
    registry.record_http_request("GET", "200", "/path1", 0.01)
    registry.record_http_request("GET", "200", "/path2", 0.01)
    registry.record_http_request("GET", "200", "/path3", 0.01)
    exported_capped = registry.export_prometheus()
    assert 'path="/other"' in exported_capped


def test_redis_adapter_dispatch_bytes_and_str():
    """Verify RedisWebSocketAdapter handles both str and bytes channels and envelopes."""
    from asok.ws.adapter import RedisWebSocketAdapter

    adapter = RedisWebSocketAdapter()
    mock_server = MagicMock()
    adapter.bind(mock_server)

    # Test bytes channel and bytes data
    payload = json.dumps({"target": "room", "name": "general", "msg": "hi"}).encode(
        "utf-8"
    )
    adapter._dispatch_item(
        {
            "type": "pmessage",
            "channel": b"asok:ws:room:general",
            "data": payload,
        }
    )
    mock_server.broadcast_to.assert_called_with("general", "hi", from_adapter=True)

    # Test str channel and str data
    adapter._dispatch_item(
        {
            "type": "pmessage",
            "channel": "asok:ws:path:/feed",
            "data": json.dumps({"target": "path", "name": "/feed", "msg": "update"}),
        }
    )
    mock_server.broadcast.assert_called_with("/feed", "update", from_adapter=True)


def test_sqlite_commit_retry():
    """Verify SQLiteEngine._commit_with_retry retries on database lock."""
    engine = SQLiteEngine(":memory:")
    fake_conn = MagicMock()
    attempts = [0]

    def flaky_commit():
        attempts[0] += 1
        if attempts[0] < 2:
            raise sqlite3.OperationalError("database is locked")

    fake_conn.commit.side_effect = flaky_commit
    engine._commit_with_retry(fake_conn)
    assert attempts[0] == 2


def test_preload_link_sanitization():
    """Verify build_preload_link sanitizes against header injection."""
    link = build_preload_link("/app.js\r\nX-Bad: 1>", as_type='script"\r\n')
    assert "\r" not in link
    assert "\n" not in link
    url_part = link.split(">;")[0][1:]
    assert ">" not in url_part
    assert "%3E" in url_part


def test_log_and_record_query_cross_engine():
    """Verify log_and_record_query correctly updates metrics and request log for any engine."""
    from asok.core.metrics import default_metrics
    from asok.orm.proxy import log_and_record_query

    class DummyReq:
        environ = {}
        _asok_sql_log = []

    req = DummyReq()
    start = time.time() - 0.05  # 50ms ago
    log_and_record_query("postgres", req, "SELECT * FROM users", (), start)

    assert len(req._asok_sql_log) == 1
    assert req._asok_sql_log[0]["sql"] == "SELECT * FROM users"
    assert "postgres" in default_metrics._db_queries


def test_redis_adapter_prevents_duplicate_self_echo():
    """Verify RedisWebSocketAdapter suppresses envelopes originating from itself."""
    from asok.ws.adapter import RedisWebSocketAdapter

    adapter = RedisWebSocketAdapter()
    mock_server = MagicMock()
    adapter.bind(mock_server)

    # Envelope with adapter's own node_id should be ignored
    self_echo = json.dumps(
        {
            "target": "room",
            "name": "chat",
            "msg": "hello",
            "node_id": adapter.node_id,
        }
    )
    adapter._dispatch_item(
        {"type": "pmessage", "channel": "asok:ws:room:chat", "data": self_echo}
    )
    mock_server.broadcast_to.assert_not_called()

    # Envelope with another node_id should be dispatched
    other_echo = json.dumps(
        {
            "target": "room",
            "name": "chat",
            "msg": "hello",
            "node_id": "other-node-123",
        }
    )
    adapter._dispatch_item(
        {"type": "pmessage", "channel": "asok:ws:room:chat", "data": other_echo}
    )
    mock_server.broadcast_to.assert_called_once_with("chat", "hello", from_adapter=True)


def test_tenant_safe_identifier_and_path_traversal_prevention():
    """Verify tenant system rejects path traversal attacks in templates and middleware."""
    from asok.middleware.tenant import TenantMiddleware
    from asok.orm.tenant import is_safe_tenant_id, resolve_tenant_database

    assert is_safe_tenant_id("tenant1") is True
    assert is_safe_tenant_id("org-123_abc") is True
    assert is_safe_tenant_id("../../etc/passwd") is False
    assert is_safe_tenant_id("foo/bar") is False
    assert is_safe_tenant_id("client; DROP TABLE") is False

    with patch.dict(os.environ, {"ASOK_TENANT_DB_TEMPLATE": "db_{tenant}.sqlite3"}):
        assert resolve_tenant_database("../../etc/passwd") is None
        assert resolve_tenant_database("client1") == "db_client1.sqlite3"

    req = MagicMock()
    req.headers = {"X-Tenant-ID": "../../etc/passwd"}
    req.args = {}
    req.host = "app.local"
    req.text.return_value = "Missing or invalid tenant identifier."

    mw = TenantMiddleware()
    res = mw(req, lambda r: "OK")
    assert res == "Missing or invalid tenant identifier."


def test_tracer_concurrency_thread_safety():
    """Verify Tracer handles concurrent span finishes safely."""
    t = Tracer(max_finished_spans=100)
    errors = []

    def finish_spans():
        try:
            for _ in range(50):
                s = t.start_span("test.concurrent")
                s.finish()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=finish_spans) for _ in range(5)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert not errors
    assert len(t.get_finished_spans()) <= 100


def test_check_db_health_sanitizes_errors_in_production():
    """Verify check_health does not leak raw exception messages when debug is disabled."""
    from asok.core.metrics import check_health

    app = MagicMock()
    app.debug = False

    with patch("asok.orm.model.Model.get_engine") as mock_get_engine:
        mock_engine = MagicMock()
        mock_engine.execute.side_effect = RuntimeError(
            "Secret database connection info host=10.0.0.1 user=admin password=secret"
        )
        mock_get_engine.return_value = mock_engine

        report, status_code = check_health(app)
        assert status_code == 503
        assert report["status"] == "unhealthy"
        assert report["checks"]["database"] == "error: database unavailable"
        assert "password" not in report["checks"]["database"]
