from __future__ import annotations

import contextvars
import json
import logging
import os
import random
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("asok.background")

_executor: Optional[ThreadPoolExecutor] = None
_lock = threading.Lock()

_LOCAL_LOCK = threading.Lock()
_LOCAL_DLQ: List[Dict[str, Any]] = []
_LOCAL_JOBS: List[Dict[str, Any]] = []


def _get_executor() -> ThreadPoolExecutor:
    """Lazy initialize the shared thread pool for background tasks."""
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=10, thread_name_prefix="asok_bg_"
                )
    return _executor


def compute_retry_delay(
    backoff: float,
    retry_count: int,
    max_delay: float = 3600.0,
    jitter: bool = True,
) -> float:
    """Calculate exponential backoff delay with randomized jitter."""
    base = min(float(max_delay), float(backoff) ** int(retry_count))
    if jitter and base > 0:
        added = random.uniform(0.0, base * 0.5)
        return round(base + added, 2)
    return round(base, 2)


def _sign_job_payload(payload: str) -> str:
    import hashlib
    import hmac

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
    import redis

    redis_url = (
        os.environ.get("ASOK_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    return redis.Redis.from_url(
        redis_url, socket_timeout=5.0, socket_connect_timeout=5.0
    )


def _is_redis_backend() -> bool:
    return os.environ.get("ASOK_QUEUE_BACKEND", "local").lower() == "redis"


def _build_job_dict(
    job_id: str,
    fn: Callable,
    args: tuple,
    kwargs: dict,
    queue: str,
    retries: int,
    backoff: int,
) -> dict:
    return {
        "id": job_id,
        "module": fn.__module__,
        "function": fn.__name__,
        "args": list(args),
        "kwargs": kwargs,
        "queue": queue,
        "retries": retries,
        "retry_count": 0,
        "backoff": backoff,
    }


def _background_redis(
    fn: Callable,
    args: tuple,
    kwargs: dict,
    queue: str = "default",
    retries: int = 0,
    backoff: int = 2,
) -> Future:
    _validate_function_for_redis(fn)
    job_id = str(uuid.uuid4())
    job = _build_job_dict(job_id, fn, args, kwargs, queue, retries, backoff)

    job_json = json.dumps(job, sort_keys=True)
    envelope = json.dumps({"v": 1, "job": job_json, "sig": _sign_job_payload(job_json)})
    client = _get_redis_client()

    status_data = {
        "status": "pending",
        "result": None,
        "error": None,
        "ts": time.time(),
        "function": f"{fn.__module__}.{fn.__name__}",
    }
    client.setex(f"asok:job:{job_id}", 86400, json.dumps(status_data))

    queue_key = "asok:queue" if queue == "default" else f"asok:queue:{queue}"
    client.lpush(queue_key, envelope)

    f = Future()
    f.job_id = job_id
    f.set_result(None)
    return f


def _record_local_job(
    job_id: str,
    status: str,
    func_name: str,
    error: Optional[str] = None,
    result: Any = None,
) -> None:
    with _LOCAL_LOCK:
        if isinstance(_LOCAL_JOBS, list):
            entry = {
                "id": job_id,
                "status": status,
                "function": func_name,
                "error": error,
                "result": result,
                "ts": time.time(),
            }
            _LOCAL_JOBS.append(entry)


def _push_local_dlq(job: dict, error_msg: str) -> None:
    with _LOCAL_LOCK:
        job_copy = dict(job)
        job_copy["failed_at"] = time.time()
        job_copy["error"] = error_msg
        _LOCAL_DLQ.insert(0, job_copy)


def _execute_local_attempt(
    fn: Callable, ctx: contextvars.Context, args: tuple, kwargs: dict
) -> Any:
    return ctx.run(fn, *args, **kwargs)


def _run_local_job_with_retries(
    fn: Callable,
    job: dict,
    ctx: contextvars.Context,
    args: tuple,
    kwargs: dict,
    retries: int,
    backoff: int,
) -> Any:
    job_id = job["id"]
    func_name = f"{fn.__module__}.{fn.__name__}"
    _record_local_job(job_id, "running", func_name)
    attempt = 0
    while True:
        try:
            res = _execute_local_attempt(fn, ctx, args, kwargs)
            _record_local_job(job_id, "completed", func_name, result=res)
            return res
        except Exception as e:
            attempt += 1
            if attempt <= retries:
                time.sleep(compute_retry_delay(backoff, attempt, max_delay=5.0))
            else:
                err_msg = f"{type(e).__name__}: {e}"
                logger.error(
                    "Background task %s failed permanently: %s", fn.__name__, e
                )
                _record_local_job(job_id, "failed", func_name, error=err_msg)
                _push_local_dlq(job, err_msg)
                return None


def _background_local(
    fn: Callable,
    executor: Optional[ThreadPoolExecutor],
    args: tuple,
    kwargs: dict,
    queue: str = "default",
    retries: int = 0,
    backoff: int = 2,
) -> Future:
    ctx = contextvars.copy_context()
    job_id = str(uuid.uuid4())
    job = _build_job_dict(job_id, fn, args, kwargs, queue, retries, backoff)
    _record_local_job(job_id, "pending", f"{fn.__module__}.{fn.__name__}")

    def wrapper() -> Any:
        return _run_local_job_with_retries(fn, job, ctx, args, kwargs, retries, backoff)

    exec_to_use = executor or _get_executor()
    f = exec_to_use.submit(wrapper)
    f.job_id = job_id
    return f


def background(
    fn: Callable,
    *args: Any,
    executor: Optional[ThreadPoolExecutor] = None,
    _queue: str = "default",
    _retries: int = 0,
    _backoff: int = 2,
    **kwargs: Any,
) -> Future:
    """Run a function in a background thread pool or Redis task queue."""
    if _is_redis_backend():
        try:
            import redis  # noqa: F401
        except ImportError:
            raise ImportError(
                "The 'redis' library is required to use the Redis queue backend."
            )
        return _background_redis(
            fn, args, kwargs, queue=_queue, retries=_retries, backoff=_backoff
        )

    return _background_local(
        fn, executor, args, kwargs, queue=_queue, retries=_retries, backoff=_backoff
    )


def task(
    fn: Optional[Callable] = None,
    *,
    queue: str = "default",
    retries: int = 0,
    backoff: int = 2,
) -> Any:
    """Decorator to mark a function as a background task.

    Provides a `.delay(*args, **kwargs)` method to enqueue the task asynchronously,
    while still allowing direct synchronous execution when called normally.

    Usage:
        @task(retries=3)
        def send_email(user_id):
            ...

        # Asynchronous background execution:
        send_email.delay(user_id)

        # Synchronous execution:
        send_email(user_id)
    """

    def decorator(func: Callable) -> Callable:
        import functools

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        def delay(*args: Any, **kwargs: Any) -> Future:
            return background(
                func,
                *args,
                _queue=queue,
                _retries=retries,
                _backoff=backoff,
                **kwargs,
            )

        setattr(wrapper, "delay", delay)
        setattr(wrapper, "is_task", True)
        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator


# ── DLQ & Monitoring APIs ─────────────────────────────────────────────


def _parse_job_item(raw_val: Any) -> Optional[dict]:
    if not raw_val:
        return None
    try:
        val_str = raw_val.decode("utf-8") if isinstance(raw_val, bytes) else raw_val
        data = json.loads(val_str)
        if "job" in data:
            return json.loads(data["job"])
        return data
    except Exception:
        return None


def _fetch_redis_dlq(offset: int, limit: int) -> List[Dict[str, Any]]:
    try:
        client = _get_redis_client()
        raw_items = client.lrange("asok:dlq", offset, offset + limit - 1)
        parsed = [_parse_job_item(item) for item in raw_items]
        return [p for p in parsed if p is not None]
    except Exception as e:
        logger.error(f"Failed to fetch DLQ from Redis: {e}")
        return []


def get_dlq_jobs(offset: int = 0, limit: int = 50) -> List[Dict[str, Any]]:
    """Return slice of failed jobs from the Dead Letter Queue."""
    if _is_redis_backend():
        return _fetch_redis_dlq(offset, limit)
    with _LOCAL_LOCK:
        return list(_LOCAL_DLQ[offset : offset + limit])


def get_dlq_count() -> int:
    """Return the total number of dead lettered tasks."""
    if _is_redis_backend():
        try:
            return int(_get_redis_client().llen("asok:dlq"))
        except Exception:
            return 0
    with _LOCAL_LOCK:
        return len(_LOCAL_DLQ)


def _safe_redis_llen(client: Any, key: str) -> int:
    try:
        return int(client.llen(key))
    except Exception:
        return 0


def _safe_redis_zcard(client: Any, key: str) -> int:
    try:
        return int(client.zcard(key))
    except Exception:
        return 0


def get_queue_counts() -> Dict[str, int]:
    """Return a dictionary of task counts across all known queues."""
    if _is_redis_backend():
        try:
            client = _get_redis_client()
            return {
                "high": _safe_redis_llen(client, "asok:queue:high"),
                "default": _safe_redis_llen(client, "asok:queue"),
                "low": _safe_redis_llen(client, "asok:queue:low"),
                "delayed": _safe_redis_zcard(client, "asok:delayed_tasks"),
                "dlq": _safe_redis_llen(client, "asok:dlq"),
            }
        except Exception:
            pass
    with _LOCAL_LOCK:
        return {"high": 0, "default": 0, "low": 0, "delayed": 0, "dlq": len(_LOCAL_DLQ)}


def _requeue_redis_job(client: Any, job: dict) -> None:
    queue = job.get("queue", "default")
    queue_key = "asok:queue" if queue == "default" else f"asok:queue:{queue}"
    job_json = json.dumps(job, sort_keys=True)
    envelope = json.dumps({"v": 1, "job": job_json, "sig": _sign_job_payload(job_json)})
    client.lpush(queue_key, envelope)


def _redis_retry_one_match(client: Any, raw: Any, job: dict, job_id: str) -> bool:
    if str(job.get("id")) == job_id:
        client.lrem("asok:dlq", 1, raw)
        job["retry_count"] = 0
        job.pop("failed_at", None)
        job.pop("error", None)
        _requeue_redis_job(client, job)
        client.setex(
            f"asok:job:{job_id}",
            86400,
            json.dumps({"status": "pending", "ts": time.time()}),
        )
        return True
    return False


def _retry_dlq_redis(job_id: str) -> bool:
    try:
        client = _get_redis_client()
        raw_items = client.lrange("asok:dlq", 0, -1)
        for raw in raw_items:
            job = _parse_job_item(raw)
            if job and _redis_retry_one_match(client, raw, job, job_id):
                return True
        return False
    except Exception as e:
        logger.error(f"Redis DLQ retry failed: {e}")
        return False


def _retry_dlq_local(job_id: str) -> bool:
    import importlib

    with _LOCAL_LOCK:
        idx = next(
            (i for i, j in enumerate(_LOCAL_DLQ) if str(j.get("id")) == job_id), None
        )
        if idx is None:
            return False
        job = _LOCAL_DLQ.pop(idx)

    try:
        mod = importlib.import_module(job["module"])
        fn = getattr(mod, job["function"])
        background(
            fn,
            *job.get("args", []),
            _queue=job.get("queue", "default"),
            _retries=job.get("retries", 0),
            _backoff=job.get("backoff", 2),
            **job.get("kwargs", {}),
        )
        return True
    except Exception as e:
        logger.error(f"Local DLQ retry failed: {e}")
        with _LOCAL_LOCK:
            _LOCAL_DLQ.insert(idx, job)
        return False


def retry_dlq_job(job_id: str) -> bool:
    """Retry a failed job from the Dead Letter Queue."""
    if _is_redis_backend():
        return _retry_dlq_redis(job_id)
    return _retry_dlq_local(job_id)


def retry_all_dlq_jobs() -> int:
    """Retry all jobs currently residing in the Dead Letter Queue."""
    jobs = get_dlq_jobs(offset=0, limit=500)
    count = 0
    for j in jobs:
        jid = str(j.get("id"))
        if jid and retry_dlq_job(jid):
            count += 1
    return count


def _delete_dlq_redis(job_id: str) -> bool:
    try:
        client = _get_redis_client()
        raw_items = client.lrange("asok:dlq", 0, -1)
        for raw in raw_items:
            job = _parse_job_item(raw)
            if job and str(job.get("id")) == job_id:
                client.lrem("asok:dlq", 1, raw)
                return True
        return False
    except Exception as e:
        logger.error(f"Redis DLQ delete failed: {e}")
        return False


def _delete_dlq_local(job_id: str) -> bool:
    with _LOCAL_LOCK:
        idx = next(
            (i for i, j in enumerate(_LOCAL_DLQ) if str(j.get("id")) == job_id), None
        )
        if idx is not None:
            _LOCAL_DLQ.pop(idx)
            return True
        return False


def delete_dlq_job(job_id: str) -> bool:
    """Permanently delete a specific failed job from the Dead Letter Queue."""
    if _is_redis_backend():
        return _delete_dlq_redis(job_id)
    return _delete_dlq_local(job_id)


def purge_dlq() -> int:
    """Clear all failed jobs from the Dead Letter Queue."""
    if _is_redis_backend():
        try:
            client = _get_redis_client()
            count = int(client.llen("asok:dlq"))
            client.delete("asok:dlq")
            return count
        except Exception:
            return 0
    with _LOCAL_LOCK:
        count = len(_LOCAL_DLQ)
        _LOCAL_DLQ.clear()
        return count
