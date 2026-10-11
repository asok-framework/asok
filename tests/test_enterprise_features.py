from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from asok import (
    TenantMiddleware,
    TracingMiddleware,
    background,
    delete_dlq_job,
    get_current_tenant,
    get_dlq_count,
    get_dlq_jobs,
    get_queue_counts,
    purge_dlq,
    register_tenant_database,
    retry_all_dlq_jobs,
    set_current_tenant,
    tenant_context,
    tracer,
)
from asok.background import compute_retry_delay
from asok.core.tracing import (
    format_traceparent,
    generate_span_id,
    generate_trace_id,
    parse_traceparent,
)
from asok.orm import Model
from asok.orm.router import route_database
from asok.orm.tenant import (
    clear_tenant_databases,
    resolve_tenant_database,
    set_tenant_resolver,
    unregister_tenant_database,
)


class DummyTenantModel(Model):
    _table = "tenant_items"


class TestMultiTenancy:
    def setup_method(self) -> None:
        clear_tenant_databases()
        set_tenant_resolver(None)
        set_current_tenant(None)

    def teardown_method(self) -> None:
        clear_tenant_databases()
        set_tenant_resolver(None)
        set_current_tenant(None)

    def test_tenant_context_manager(self) -> None:
        assert get_current_tenant() is None
        with tenant_context("acme"):
            assert get_current_tenant() == "acme"
            with tenant_context("beta"):
                assert get_current_tenant() == "beta"
            assert get_current_tenant() == "acme"
        assert get_current_tenant() is None

    def test_tenant_database_registry_and_resolver(self) -> None:
        register_tenant_database("acme", "sqlite:///tmp/acme.db")
        assert resolve_tenant_database("acme") == "sqlite:///tmp/acme.db"
        assert resolve_tenant_database("ACME") == "sqlite:///tmp/acme.db"

        # Resolver callback fallback
        set_tenant_resolver(lambda t: f"postgres://user:pass@localhost:5432/{t}")
        assert (
            resolve_tenant_database("megacorp")
            == "postgres://user:pass@localhost:5432/megacorp"
        )

        unregister_tenant_database("acme")
        assert (
            resolve_tenant_database("acme")
            == "postgres://user:pass@localhost:5432/acme"
        )

    def test_tenant_template_resolution(self) -> None:
        with patch.dict(os.environ, {"ASOK_TENANT_DB_TEMPLATE": "db_{tenant}.sqlite3"}):
            assert resolve_tenant_database("client1") == "db_client1.sqlite3"

    def test_tenant_router_integration(self) -> None:
        register_tenant_database("tenant_a", "sqlite:///tmp/tenant_a.db")
        register_tenant_database("tenant_b", "sqlite:///tmp/tenant_b.db")

        # Without tenant active -> falls back to default
        default_route = route_database(DummyTenantModel, "read")
        assert default_route != "sqlite:///tmp/tenant_a.db"

        # With tenant_a active
        with tenant_context("tenant_a"):
            routed_read = route_database(DummyTenantModel, "read")
            routed_write = route_database(DummyTenantModel, "write")
            assert routed_read == "sqlite:///tmp/tenant_a.db"
            assert routed_write == "sqlite:///tmp/tenant_a.db"

        # With tenant_b active
        with tenant_context("tenant_b"):
            routed_read = route_database(DummyTenantModel, "read")
            assert routed_read == "sqlite:///tmp/tenant_b.db"

        # Context exited -> back to default
        assert route_database(DummyTenantModel, "read") == default_route

    def test_tenant_middleware_header_extraction(self) -> None:
        req = MagicMock()
        req.headers = {"X-Tenant-ID": "acme_corp"}
        req.args = {}
        req.host = "app.example.com"

        called = []

        def next_handler(request):
            called.append(request.tenant)
            assert get_current_tenant() == "acme_corp"
            return "OK"

        mw = TenantMiddleware()
        res = mw(req, next_handler)
        assert res == "OK"
        assert called == ["acme_corp"]
        assert get_current_tenant() is None

    def test_tenant_middleware_subdomain_extraction(self) -> None:
        req = MagicMock()
        req.headers = {}
        req.args = {}
        req.host = "acme.platform.io"

        called = []

        def next_handler(request):
            called.append(request.tenant)
            return "OK"

        mw = TenantMiddleware(extract_subdomain=True, base_domain="platform.io")
        res = mw(req, next_handler)
        assert res == "OK"
        assert called == ["acme"]

    def test_tenant_middleware_strict_rejection(self) -> None:
        req = MagicMock()
        req.headers = {}
        req.args = {}
        req.host = "localhost"
        req.text.return_value = "Missing or invalid tenant identifier."

        mw = TenantMiddleware(strict=True)
        res = mw(req, lambda r: "OK")
        assert res == "Missing or invalid tenant identifier."
        req.text.assert_called_with("Missing or invalid tenant identifier.", status=400)


class TestDistributedTracing:
    def test_generate_and_parse_traceparent(self) -> None:
        t_id = generate_trace_id()
        s_id = generate_span_id()
        assert len(t_id) == 32
        assert len(s_id) == 16

        tp = format_traceparent(t_id, s_id, "01")
        assert tp.startswith("00-")

        parsed = parse_traceparent(tp)
        assert parsed is not None
        assert parsed[0] == t_id
        assert parsed[1] == s_id
        assert parsed[2] == "01"

    def test_parse_invalid_traceparents(self) -> None:
        # None or empty
        assert parse_traceparent(None) is None
        assert parse_traceparent("") is None

        # Wrong format
        assert parse_traceparent("invalid-header") is None

        # All zeros
        zero_trace = f"00-{'0' * 32}-{'1' * 16}-01"
        assert parse_traceparent(zero_trace) is None

        zero_span = f"00-{'1' * 32}-{'0' * 16}-01"
        assert parse_traceparent(zero_span) is None

        # Non-00 version
        v1_trace = f"01-{'a' * 32}-{'b' * 16}-01"
        assert parse_traceparent(v1_trace) is None

    def test_span_lifecycle_and_context(self) -> None:
        tracer.clear_spans()
        with tracer.start_span("root_op") as root:
            root.set_attribute("env", "test")
            root.add_event("stage_1", {"items": 5})

            with tracer.start_span("child_op") as child:
                child.set_attribute("query", "SELECT 1")
                assert child.parent_span_id == root.span_id
                assert child.trace_id == root.trace_id

            assert child.end_time is not None
            assert child.duration_ms is not None
            assert child.status == "ok"

        assert root.end_time is not None
        assert root.status == "ok"

        spans = tracer.get_finished_spans()
        assert len(spans) == 2
        spans_dict = [s.to_dict() for s in spans]
        assert any(d["name"] == "child_op" for d in spans_dict)
        assert any(d["name"] == "root_op" for d in spans_dict)

    def test_span_exception_recording(self) -> None:
        with pytest.raises(ValueError):
            with tracer.start_span("error_op") as span:
                raise ValueError("Something went wrong")

        assert span.status == "error"
        assert any(e["name"] == "exception" for e in span.events)

    def test_tracing_middleware(self) -> None:
        req = MagicMock()
        req.headers = {}
        req.method = "GET"
        req.path = "/api/users"
        req.ip = "192.168.1.10"
        req.host = "api.service.local"
        req.user_agent = "pytest"

        resp = MagicMock()
        resp.headers = {}
        resp.status_code = 200

        def handler(r):
            assert hasattr(r, "trace_id")
            assert hasattr(r, "span_id")
            return resp

        mw = TracingMiddleware()
        res = mw(req, handler)
        assert res is resp

        assert "traceparent" in resp.headers
        assert resp.headers["X-Trace-ID"] == req.trace_id
        assert resp.headers["X-Span-ID"] == req.span_id


class TestAdvancedJobQueueAndDLQ:
    def setup_method(self) -> None:
        purge_dlq()

    def teardown_method(self) -> None:
        purge_dlq()

    def test_compute_retry_delay_with_jitter(self) -> None:
        delay_no_jitter = compute_retry_delay(2.0, 3, jitter=False)
        assert delay_no_jitter == 8.0

        delay_jitter = compute_retry_delay(2.0, 3, jitter=True)
        # 8 <= delay <= 8 + 4 = 12
        assert 8.0 <= delay_jitter <= 12.0

        # Max delay cap
        delay_capped = compute_retry_delay(10.0, 5, max_delay=30.0, jitter=False)
        assert delay_capped == 30.0

    def test_local_task_failure_and_dlq(self) -> None:
        def failing_task():
            raise RuntimeError("DB connection timeout")

        purge_dlq()
        fut = background(failing_task, _retries=1, _backoff=1)

        # The future completes safely without crashing the caller
        res = fut.result(timeout=2.0)
        assert res is None

        # Job must now be present in DLQ
        dlq_jobs = get_dlq_jobs()
        assert len(dlq_jobs) >= 1
        job = dlq_jobs[0]
        assert "failing_task" in job["function"]
        assert "DB connection timeout" in job["error"]

        count = get_dlq_count()
        assert count >= 1

        counts = get_queue_counts()
        assert counts["dlq"] >= 1

        # Delete job from DLQ
        job_id = job["id"]
        assert delete_dlq_job(job_id) is True
        assert get_dlq_count() == count - 1

    def test_dlq_retry_and_purge(self) -> None:
        from asok.background import _push_local_dlq

        purge_dlq()
        assert get_dlq_count() == 0

        dummy_job = {
            "id": "job-test-123",
            "module": "tests.test_enterprise_features",
            "function": "sample_retry_task",
            "args": [42],
            "kwargs": {"name": "test"},
            "queue": "default",
            "retries": 0,
        }
        _push_local_dlq(dummy_job, "Failed on first attempt")

        assert get_dlq_count() == 1
        jobs = get_dlq_jobs()
        assert jobs[0]["id"] == "job-test-123"

        # Retry all
        retried = retry_all_dlq_jobs()
        assert retried == 1
        assert get_dlq_count() == 0

        # Purge
        _push_local_dlq(dummy_job, "Another error")
        assert get_dlq_count() == 1
        purged = purge_dlq()
        assert purged == 1
        assert get_dlq_count() == 0


def sample_retry_task(arg, name=""):
    return f"{name}:{arg}"
