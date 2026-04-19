import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

logger = logging.getLogger("asok.background")

_executor: Optional[ThreadPoolExecutor] = None
_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """Lazy initialize the shared thread pool for background tasks."""
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                # Default to a safe number of workers to prevent resource exhaustion
                _executor = ThreadPoolExecutor(
                    max_workers=10, thread_name_prefix="asok_bg_"
                )
    return _executor


def background(fn: Callable, *args: Any, **kwargs: Any) -> Future:
    """Run a function in a background thread pool (fire-and-forget).

    Tasks are queued and executed by a bounded pool of workers to prevent
    resource exhaustion (DoS protection).

    The request responds immediately. Errors are caught and logged, not raised.

    Returns:
        A concurrent.futures.Future object representing the execution.
    """

    def wrapper() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            logger.error("Background task %s failed: %s", fn.__name__, e)

    return _get_executor().submit(wrapper)
