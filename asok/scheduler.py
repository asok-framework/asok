from __future__ import annotations

import logging
import threading
from typing import Any, Callable

logger = logging.getLogger("asok.scheduler")


class ScheduledTask:
    """Represents a recurring task running in a background thread."""

    def __init__(
        self, interval: float, fn: Callable, args: tuple, kwargs: dict[str, Any]
    ):
        """Initialize and start the scheduled task thread."""
        self._interval = interval
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        """Internal loop that executes the function at the specified interval."""
        while not self._stop_event.wait(timeout=self._interval):
            try:
                self._fn(*self._args, **self._kwargs)
            except Exception:
                logger.exception("Scheduled task %s failed", self._fn.__name__)

    def cancel(self) -> None:
        """Stop the scheduled task from recurring."""
        self._stop_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Return True if the task has been cancelled."""
        return self._stop_event.is_set()


def schedule(seconds: float, fn: Callable, *args: Any, **kwargs: Any) -> ScheduledTask:
    """Convenience factory to create and start a recurring scheduled task."""
    return ScheduledTask(seconds, fn, args, kwargs)
