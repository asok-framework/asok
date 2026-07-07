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


def _sign_job_payload(payload: str) -> str:
    import hashlib
    import hmac
    import os

    key = os.environ.get("SECRET_KEY", "").encode()
    if not key:
        raise RuntimeError(
            "SECRET_KEY is required to sign Redis jobs. "
            "Set the SECRET_KEY environment variable."
        )
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _validate_function_for_redis(fn: Callable) -> None:
    if fn.__name__ == "<lambda>" or "<locals>" in fn.__qualname__:
        raise ValueError("Only module-level functions can be queued on Redis.")


def _get_redis_client() -> Any:
    import os

    import redis

    redis_url = (
        os.environ.get("ASOK_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    return redis.Redis.from_url(
        redis_url, socket_timeout=5.0, socket_connect_timeout=5.0
    )


def _background_redis(
    fn: Callable,
    args: tuple,
    kwargs: dict,
    queue: str = "default",
    retries: int = 0,
    backoff: int = 2,
) -> Future:
    import json
    import time
    import uuid

    _validate_function_for_redis(fn)

    module_name = fn.__module__
    func_name = fn.__name__
    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "module": module_name,
        "function": func_name,
        "args": args,
        "kwargs": kwargs,
        "queue": queue,
        "retries": retries,
        "retry_count": 0,
        "backoff": backoff,
    }

    job_json = json.dumps(job, sort_keys=True)
    envelope = json.dumps({"v": 1, "job": job_json, "sig": _sign_job_payload(job_json)})
    client = _get_redis_client()

    # Initialize status in Redis as pending (Result Backend) with 24h TTL
    status_data = {
        "status": "pending",
        "result": None,
        "error": None,
        "ts": time.time(),
        "function": f"{module_name}.{func_name}",
    }
    client.setex(f"asok:job:{job_id}", 86400, json.dumps(status_data))

    # Push to specific queue list (default to asok:queue)
    queue_key = "asok:queue" if queue == "default" else f"asok:queue:{queue}"
    client.lpush(queue_key, envelope)

    f = Future()
    f.job_id = job_id
    f.set_result(None)
    return f


def _background_local(
    fn: Callable,
    executor: Optional[ThreadPoolExecutor],
    args: tuple,
    kwargs: dict,
) -> Future:
    import contextvars

    ctx = contextvars.copy_context()

    def wrapper() -> Any:
        try:
            return ctx.run(fn, *args, **kwargs)
        except Exception as e:
            logger.error("Background task %s failed: %s", fn.__name__, e)

    exec_to_use = executor or _get_executor()
    return exec_to_use.submit(wrapper)


def background(
    fn: Callable,
    *args: Any,
    executor: Optional[ThreadPoolExecutor] = None,
    _queue: str = "default",
    _retries: int = 0,
    _backoff: int = 2,
    **kwargs: Any,
) -> Future:
    """Run a function in a background thread pool or Redis task queue.

    Args:
        fn: The function to execute.
        *args: Positional arguments for the function.
        executor: Optional executor to use (defaults to shared pool, local only).
        _queue: Target queue priority name (defaults to "default").
        _retries: Number of retries on failure (defaults to 0).
        _backoff: Multiplier for exponential backoff retry delays (defaults to 2).
        **kwargs: Keyword arguments for the function.

    Returns:
        A concurrent.futures.Future object (having .job_id if Redis backend is used).
    """
    import os

    backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
    if backend == "redis":
        try:
            import redis  # noqa: F401
        except ImportError:
            raise ImportError(
                "The 'redis' library is required to use the Redis queue backend. "
                "Install it using 'pip install asok[redis]'."
            )
        return _background_redis(
            fn, args, kwargs, queue=_queue, retries=_retries, backoff=_backoff
        )

    return _background_local(fn, executor, args, kwargs)
