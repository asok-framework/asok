from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from asok.background import background
from asok.cli.worker import _execute_job_in_thread, _run_worker_loop, show_queue_status
from asok.scheduler import ScheduledTask


@pytest.fixture
def mock_redis():
    mock_module = MagicMock()
    mock_client = MagicMock()
    mock_module.Redis.from_url.return_value = mock_client
    mock_module.exceptions = MagicMock()

    # Simulate zrem returning 1 (success)
    mock_client.zrem.return_value = 1
    # Simulate set returning True (success) for lock
    mock_client.set.return_value = True

    with patch.dict(sys.modules, {"redis": mock_module}):
        yield mock_client


def dummy_success(x, y):
    return x + y


def dummy_failure():
    raise ValueError("Something went wrong")


def test_background_redis_metadata_and_status(mock_redis) -> None:
    secret = "a" * 32
    with patch.dict(
        os.environ,
        {
            "ASOK_QUEUE_BACKEND": "redis",
            "ASOK_REDIS_URL": "redis://localhost:6379/1",
            "SECRET_KEY": secret,
        },
    ):
        future = background(
            dummy_success, 10, 20, _queue="high", _retries=3, _backoff=2
        )
        assert hasattr(future, "job_id")
        job_id = future.job_id

        # Verify status initialized as pending in Redis
        mock_redis.setex.assert_any_call(
            f"asok:job:{job_id}",
            86400,
            pytest.approx_json_contains(
                {
                    "status": "pending",
                    "function": "dummy_success",
                }
            ),
        )

        # Verify job pushed to high priority queue
        mock_redis.lpush.assert_called_once()
        called_args = mock_redis.lpush.call_args[0]
        assert called_args[0] == "asok:queue:high"


def test_worker_executes_success_and_updates_status(mock_redis) -> None:
    secret = "a" * 32
    job = {
        "id": "test-job-uuid",
        "module": "tests.test_advanced_performance",
        "function": "dummy_success",
        "args": [5, 10],
        "kwargs": {},
        "queue": "default",
        "retries": 0,
        "retry_count": 0,
        "backoff": 2,
    }
    job_json = json.dumps(job, sort_keys=True)
    from asok.background import _sign_job_payload

    with patch.dict(os.environ, {"SECRET_KEY": secret}):
        sig = _sign_job_payload(job_json)
        envelope = json.dumps({"v": 1, "job": job_json, "sig": sig}).encode("utf-8")

        _execute_job_in_thread(mock_redis, envelope)

        # Check status transitions
        mock_redis.setex.assert_any_call(
            "asok:job:test-job-uuid",
            86400,
            pytest.approx_json_contains({"status": "running"}),
        )
        mock_redis.setex.assert_any_call(
            "asok:job:test-job-uuid",
            86400,
            pytest.approx_json_contains({"status": "completed", "result": 15}),
        )


def test_worker_failure_triggers_retry(mock_redis) -> None:
    secret = "a" * 32
    job = {
        "id": "test-fail-uuid",
        "module": "tests.test_advanced_performance",
        "function": "dummy_failure",
        "args": [],
        "kwargs": {},
        "queue": "default",
        "retries": 2,
        "retry_count": 0,
        "backoff": 2,
    }
    job_json = json.dumps(job, sort_keys=True)
    from asok.background import _sign_job_payload

    with patch.dict(os.environ, {"SECRET_KEY": secret}):
        sig = _sign_job_payload(job_json)
        envelope = json.dumps({"v": 1, "job": job_json, "sig": sig}).encode("utf-8")

        _execute_job_in_thread(mock_redis, envelope)

        # Check status was set to retrying
        mock_redis.setex.assert_any_call(
            "asok:job:test-fail-uuid",
            86400,
            pytest.approx_json_contains({"status": "retrying"}),
        )

        # Verify job was scheduled in sorted set
        mock_redis.zadd.assert_called_once()
        zadd_args = mock_redis.zadd.call_args[0]
        assert zadd_args[0] == "asok:delayed_tasks"


def test_worker_failure_exhausts_retries_and_sends_to_dlq(mock_redis) -> None:
    secret = "a" * 32
    # Already executed twice, max retries = 2
    job = {
        "id": "test-exhausted-uuid",
        "module": "tests.test_advanced_performance",
        "function": "dummy_failure",
        "args": [],
        "kwargs": {},
        "queue": "default",
        "retries": 2,
        "retry_count": 2,
        "backoff": 2,
    }
    job_json = json.dumps(job, sort_keys=True)
    from asok.background import _sign_job_payload

    with patch.dict(os.environ, {"SECRET_KEY": secret}):
        sig = _sign_job_payload(job_json)
        envelope = json.dumps({"v": 1, "job": job_json, "sig": sig}).encode("utf-8")

        _execute_job_in_thread(mock_redis, envelope)

        # Check status is failed
        mock_redis.setex.assert_any_call(
            "asok:job:test-exhausted-uuid",
            86400,
            pytest.approx_json_contains({"status": "failed"}),
        )

        # Verify pushed to DLQ
        mock_redis.lpush.assert_called_with(
            "asok:dlq",
            pytest.approx_json_contains(
                {
                    "id": "test-exhausted-uuid",
                    "error": "ValueError: Something went wrong",
                }
            ),
        )


def test_scheduler_acquires_lock_successfully(mock_redis) -> None:
    # Lock is successfully acquired
    mock_redis.set.return_value = True

    called = []

    def task():
        called.append(True)

    with patch.dict(os.environ, {"ASOK_QUEUE_BACKEND": "redis"}):
        t = ScheduledTask(0.1, task)
        time.sleep(0.15)
        t.cancel()

    assert len(called) >= 1
    mock_redis.set.assert_called()
    called_args = mock_redis.set.call_args[0]
    assert "asok:lock:scheduler:" in called_args[0]


def test_scheduler_lock_collision_skips_execution(mock_redis) -> None:
    # Lock fails (already acquired by another instance)
    mock_redis.set.return_value = False

    called = []

    def task():
        called.append(True)

    with patch.dict(os.environ, {"ASOK_QUEUE_BACKEND": "redis"}):
        t = ScheduledTask(0.1, task)
        time.sleep(0.15)
        t.cancel()

    # The task should not be called since lock acquisition failed
    assert len(called) == 0


def test_worker_queues_prioritization(mock_redis) -> None:
    # Simulate a loop that finishes quickly
    mock_redis.brpop.side_effect = KeyboardInterrupt()

    with patch.dict(
        os.environ,
        {"ASOK_WORKER_QUEUES": "high,default,low", "ASOK_WORKER_CONCURRENCY": "2"},
    ):
        try:
            _run_worker_loop(mock_redis, "redis://localhost:6379")
        except KeyboardInterrupt:
            pass

        # Verify queues checked in priority order
        mock_redis.brpop.assert_called_with(
            ["asok:queue:high", "asok:queue", "asok:queue:low"], timeout=5
        )


def test_worker_status_command_output(mock_redis, capsys) -> None:
    mock_redis.llen.return_value = 0
    show_queue_status(mock_redis, "redis://localhost:6379")
    captured = capsys.readouterr().out
    import re

    clean_captured = re.sub(r"\x1b\[[0-9;]*m", "", captured)
    assert "Pending tasks: 0" in clean_captured
    assert "asok:queue:high" in clean_captured
    assert "asok:queue:default" in clean_captured
    assert "asok:queue:low" in clean_captured
    assert "asok:dlq" in clean_captured


# Custom pytest helper to approx match JSON strings in mock assertions
class ApproxJsonContains:
    def __init__(self, expected_dict):
        self.expected = expected_dict

    def __eq__(self, other):
        if not isinstance(other, (str, bytes)):
            return False
        if isinstance(other, bytes):
            other = other.decode("utf-8")
        try:
            data = json.loads(other)
            for k, v in self.expected.items():
                if k not in data or data[k] != v:
                    # Special check for subset contains
                    if isinstance(v, str) and v in str(data.get(k, "")):
                        continue
                    return False
            return True
        except Exception:
            return False

    def __repr__(self):
        return f"ApproxJsonContains({self.expected})"


pytest.approx_json_contains = ApproxJsonContains
