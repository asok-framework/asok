"""
Tests for the scheduler module.
Covers: schedule function, ScheduledTask thread lifecycle, cancellation.
"""

import time

from asok.scheduler import ScheduledTask, schedule


class TestScheduler:
    def test_task_executes_repeatedly(self):
        execution_count = 0

        def my_task():
            nonlocal execution_count
            execution_count += 1

        # Schedule every 0.05 seconds
        task = schedule(0.05, my_task)
        assert isinstance(task, ScheduledTask)

        # Wait enough time for 2-3 executions
        time.sleep(0.12)

        task.cancel()

        # It should have run at least twice
        assert execution_count >= 2

    def test_task_cancellation_stops_execution(self):
        execution_count = 0

        def my_task():
            nonlocal execution_count
            execution_count += 1

        task = schedule(0.05, my_task)
        time.sleep(0.06)  # Let it run once

        task.cancel()
        count_at_cancel = execution_count

        time.sleep(0.1)  # Wait longer

        # Should not have incremented after cancellation
        assert execution_count == count_at_cancel

    def test_args_and_kwargs_passed_to_task(self):
        captured = []

        def my_task(x, y, multiplier=1):
            captured.append((x + y) * multiplier)

        task = schedule(0.02, my_task, 3, 4, multiplier=2)
        time.sleep(0.03)
        task.cancel()

        assert len(captured) >= 1
        assert captured[0] == 14

    def test_exceptions_do_not_kill_scheduler(self):
        """A crashing task should be caught and the loop should continue."""
        execution_count = 0

        def crashing_task():
            nonlocal execution_count
            execution_count += 1
            if execution_count == 1:
                raise ValueError("Intentional crash")

        task = schedule(0.02, crashing_task)
        time.sleep(0.06)
        task.cancel()

        # It should have survived the first crash and run again
        assert execution_count >= 2
