from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger("asok.scheduler")


def _get_scheduler_redis_client() -> Any:
    import os

    import redis

    redis_url = (
        os.environ.get("ASOK_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    return redis.Redis.from_url(
        redis_url, socket_timeout=2.0, socket_connect_timeout=2.0
    )


class ScheduledTask:
    """Represents a recurring task running in a background thread."""

    def __init__(
        self,
        interval: str | float,
        fn: Callable,
        args: Optional[tuple] = None,
        kwargs: Optional[dict[str, Any]] = None,
    ):
        """Initialize and start the scheduled task thread."""
        if isinstance(interval, str):
            self._interval = self._parse_interval(interval)
        else:
            self._interval = float(interval)

        self._fn = fn
        self._args = args or ()
        self._kwargs = kwargs or {}
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @staticmethod
    def _parse_interval(interval_str: str) -> float:
        """Parse interval strings like '5m', '1h', '1w', '1mo', '1y' into seconds.

        SECURITY: Validates interval bounds to prevent DoS with extreme values.
        """
        s = interval_str.lower().strip()

        # SECURITY: Limit input string length to prevent parsing DoS
        if len(s) > 20:
            return 60.0

        try:
            result = ScheduledTask._parse_unit(s)
            # SECURITY: Validate interval bounds
            # Minimum: 1 second (prevent tight loops)
            # Maximum: 1 year (prevent overflow and unreasonable schedules)
            if result < 1.0 or result > 365 * 86400:  # 1 year max
                return 60.0

            return result
        except (ValueError, IndexError, OverflowError):
            return 60.0

    @staticmethod
    def _parse_unit(s: str) -> float:
        # Multi-character suffixes (mo)
        if s.endswith("mo"):
            val = float(s[:-2])
            return val * 30 * 86400
        # Single-character suffixes
        val = float(s[:-1])
        unit = s[-1]
        multiplier = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 7 * 86400,
            "y": 365 * 86400,
        }.get(unit, 1)
        return val * multiplier

    def _acquire_distributed_lock(self) -> bool:
        import os

        backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
        if backend != "redis":
            return True

        try:
            client = _get_scheduler_redis_client()
            module_name = self._fn.__module__
            func_name = self._fn.__name__
            lock_key = f"asok:lock:scheduler:{module_name}:{func_name}"

            lock_ttl = min(3600, max(1, int(self._interval * 0.9)))
            acquired = client.set(lock_key, "1", ex=lock_ttl, nx=True)
            return bool(acquired)
        except Exception as e:
            logger.warning(
                f"Failed to acquire scheduler lock for {self._fn.__name__}: {e}"
            )
            return True

    def _run(self) -> None:
        """Internal loop that executes the function at the specified interval."""
        while not self._stop_event.wait(timeout=self._interval):
            if not self._acquire_distributed_lock():
                continue
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


def schedule(
    interval: str | float, fn: Optional[Callable] = None, *args: Any, **kwargs: Any
) -> Any:
    """Create and start a recurring scheduled task.
    Can be used as a function or as a decorator.

    Usage:
        # As a function:
        schedule("5m", my_task)

        # As a decorator:
        @schedule("1h")
        def periodic_cleanup():
            ...
    """

    def decorator(func: Callable) -> ScheduledTask:
        return ScheduledTask(interval, func, args, kwargs)

    if fn is None:
        return decorator

    return ScheduledTask(interval, fn, args, kwargs)
