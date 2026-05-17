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


def background(
    fn: Callable,
    *args: Any,
    executor: Optional[ThreadPoolExecutor] = None,
    **kwargs: Any,
) -> Future:
    """Run a function in a background thread pool (fire-and-forget).

    Args:
        fn: The function to execute.
        *args: Positional arguments for the function.
        executor: Optional executor to use (defaults to shared pool).
        **kwargs: Keyword arguments for the function.

    Returns:
        A concurrent.futures.Future object.
    """

    def wrapper() -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:
            logger.error("Background task %s failed: %s", fn.__name__, e)

    exec_to_use = executor or _get_executor()
    return exec_to_use.submit(wrapper)
