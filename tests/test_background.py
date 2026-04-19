"""
Tests for the background task runner.
background() is a function (not a decorator) that takes fn + args and returns a Future.
"""

import time
from concurrent.futures import Future

from asok.background import background


class TestBackgroundTasks:
    def test_returns_future(self):
        """background() must return a Future."""
        result = background(lambda: None)
        assert isinstance(result, Future)

    def test_function_runs_in_background(self):
        """A background task should execute without blocking."""
        results = []

        def slow_task():
            time.sleep(0.05)
            results.append("done")

        start = time.time()
        f = background(slow_task)
        elapsed = time.time() - start

        # Should return almost immediately (non-blocking)
        assert elapsed < 0.04

        # Wait for the background thread to finish
        f.result(timeout=2)
        assert results == ["done"]

    def test_function_receives_arguments(self):
        """Arguments must be correctly passed to the background function."""
        captured = []

        def task_with_args(x, y):
            captured.append(x + y)

        f = background(task_with_args, 3, 4)
        f.result(timeout=2)
        assert captured == [7]

    def test_function_receives_kwargs(self):
        """Keyword arguments must be correctly passed."""
        captured = []

        def task_with_kwargs(name="world"):
            captured.append(f"Hello, {name}!")

        f = background(task_with_kwargs, name="Asok")
        f.result(timeout=2)
        assert captured == ["Hello, Asok!"]

    def test_multiple_tasks_run(self):
        """Multiple background tasks should all complete."""
        results = []

        def task(label):
            results.append(label)

        f1 = background(task, "first")
        f2 = background(task, "second")
        f1.result(timeout=2)
        f2.result(timeout=2)
        assert "first" in results
        assert "second" in results

    def test_exception_does_not_crash_caller(self):
        """A task that raises should not propagate to the caller."""

        def bad_task():
            raise ValueError("Intentional error in background task")

        # Should not raise
        f = background(bad_task)
        # The future completes (the wrapper catches the error)
        f.result(timeout=2)
