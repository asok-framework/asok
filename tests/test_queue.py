from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

from asok.background import background

_dummy_task_executed = False


def dummy_task(arg1, kwarg1=None) -> None:
    global _dummy_task_executed
    _dummy_task_executed = (arg1, kwarg1)


def test_redis_queue_enqueue() -> None:
    mock_redis = MagicMock()
    mock_client = MagicMock()
    mock_redis.Redis.from_url.return_value = mock_client

    with patch.dict(sys.modules, {"redis": mock_redis}):
        with patch.dict(
            os.environ,
            {
                "ASOK_QUEUE_BACKEND": "redis",
                "ASOK_REDIS_URL": "redis://localhost:6379/1",
            },
        ):
            future = background(dummy_task, "val1", kwarg1="val2")
            assert future is not None

            mock_client.lpush.assert_called_once()
            called_args = mock_client.lpush.call_args[0]
            assert called_args[0] == "asok:queue"

            job = json.loads(called_args[1])
            assert job["module"] == dummy_task.__module__
            assert job["function"] == "dummy_task"
            assert job["args"] == ["val1"]
            assert job["kwargs"] == {"kwarg1": "val2"}


def test_worker_loop() -> None:
    mock_redis = MagicMock()
    mock_client = MagicMock()
    mock_redis.Redis.from_url.return_value = mock_client

    job_data = {
        "module": dummy_task.__module__,
        "function": "dummy_task",
        "args": ["val1"],
        "kwargs": {"kwarg1": "val2"},
    }

    # mock brpop to return task once, then raise KeyboardInterrupt to break the worker loop
    mock_client.brpop.side_effect = [
        (b"asok:queue", json.dumps(job_data).encode("utf-8")),
        KeyboardInterrupt(),
    ]

    from asok.cli.worker import run_worker

    with patch.dict(sys.modules, {"redis": mock_redis}):
        with patch.dict(os.environ, {"ASOK_QUEUE_BACKEND": "redis"}):
            global _dummy_task_executed
            _dummy_task_executed = False

            run_worker()

            assert _dummy_task_executed == ("val1", "val2")
