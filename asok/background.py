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
    """Run a function in a background thread pool or Redis task queue (fire-and-forget).

    Args:
        fn: The function to execute.
        *args: Positional arguments for the function.
        executor: Optional executor to use (defaults to shared pool, local only).
        **kwargs: Keyword arguments for the function.

    Returns:
        A concurrent.futures.Future object.
    """
    import os

    backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
    if backend == "redis":
        try:
            import redis
        except ImportError:
            raise ImportError(
                "The 'redis' library is required to use the Redis queue backend. "
                "Install it using 'pip install asok[redis]'."
            )

        module_name = fn.__module__
        func_name = fn.__name__

        if func_name == "<lambda>" or "<locals>" in fn.__qualname__:
            raise ValueError("Only module-level functions can be queued on Redis.")

        job = {
            "module": module_name,
            "function": func_name,
            "args": args,
            "kwargs": kwargs,
        }

        import json

        redis_url = os.environ.get("ASOK_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://localhost:6379/0"
        client = redis.Redis.from_url(redis_url)
        client.lpush("asok:queue", json.dumps(job))

        f = Future()
        f.set_result(None)
        return f

    import contextvars
    ctx = contextvars.copy_context()

    def wrapper() -> Any:
        try:
            return ctx.run(fn, *args, **kwargs)
        except Exception as e:
            logger.error("Background task %s failed: %s", fn.__name__, e)

    exec_to_use = executor or _get_executor()
    return exec_to_use.submit(wrapper)
