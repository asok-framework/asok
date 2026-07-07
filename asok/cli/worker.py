from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import threading
import time
from typing import Any

logger = logging.getLogger("asok.worker")


def _get_redis_url() -> str:
    url = os.environ.get("ASOK_REDIS_URL") or os.environ.get("REDIS_URL")
    return url if url else "redis://localhost:6379/0"


def _init_redis_client() -> tuple[Any, str]:
    backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
    if backend != "redis":
        print(
            "Error: ASOK_QUEUE_BACKEND must be set to 'redis' to use worker commands."
        )
        sys.exit(1)

    try:
        import redis
    except ImportError:
        print("Error: The 'redis' package is required. Run 'pip install asok[redis]'.")
        sys.exit(1)

    redis_url = _get_redis_url()

    try:
        return (
            redis.Redis.from_url(
                redis_url, socket_timeout=5.0, socket_connect_timeout=5.0
            ),
            redis_url,
        )
    except Exception as e:
        print(f"Error connecting to Redis: {e}")
        sys.exit(1)


def _verify_job_signature(job_json: str, sig: str) -> bool:
    import hashlib
    import hmac as _hmac

    key = os.environ.get("SECRET_KEY", "").encode()
    if not key:
        logger.error("SECRET_KEY not set — cannot verify job signature")
        return False
    expected = _hmac.new(key, job_json.encode(), hashlib.sha256).hexdigest()
    return _hmac.compare_digest(expected, sig)


def _verify_envelope(envelope: dict[str, Any]) -> bool:
    if "sig" not in envelope or "job" not in envelope:
        print("[!] Rejected unsigned job — missing signature envelope")
        logger.error("Rejected unsigned Redis job (no signature). Possible tampering.")
        return False

    if not _verify_job_signature(envelope["job"], envelope["sig"]):
        print("[!] Rejected job: invalid signature")
        logger.error(
            "Rejected Redis job with invalid HMAC signature. Possible tampering."
        )
        return False

    return True


def _parse_and_verify_job(job_data: bytes) -> dict[str, Any] | None:
    try:
        envelope = json.loads(job_data.decode("utf-8"))
    except Exception as e:
        print(f"[!] Rejected malformed job data: {e}")
        return None

    if not _verify_envelope(envelope):
        return None

    try:
        return json.loads(envelope["job"])
    except Exception as e:
        print(f"[!] Rejected job: invalid JSON payload: {e}")
        return None


def _parse_raw_job_data(raw: Any) -> dict[str, Any]:
    if not raw or not isinstance(raw, (str, bytes, bytearray)):
        return {}
    raw_str = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    try:
        return json.loads(raw_str)
    except Exception:
        return {}


def _update_job_status(
    client: Any,
    job_id: str,
    status: str,
    result: Any = None,
    error: str | None = None,
) -> None:
    try:
        raw = client.get(f"asok:job:{job_id}")
        data = _parse_raw_job_data(raw)
        data.update(
            {
                "status": status,
                "result": result,
                "error": error,
                "ts": time.time(),
            }
        )
        client.setex(f"asok:job:{job_id}", 86400, json.dumps(data))
    except Exception as e:
        logger.error(f"Failed to update job status: {e}")


def _schedule_job_retry(client: Any, job: dict[str, Any], error_msg: str) -> None:
    job_id = job.get("id")
    retry_count = job.get("retry_count", 0) + 1
    job["retry_count"] = retry_count
    backoff = job.get("backoff", 2)
    delay = backoff**retry_count
    run_at = time.time() + delay

    from asok.background import _sign_job_payload

    job_json = json.dumps(job, sort_keys=True)
    envelope = json.dumps(
        {
            "v": 1,
            "job": job_json,
            "sig": _sign_job_payload(job_json),
        }
    )

    client.zadd("asok:delayed_tasks", {envelope: run_at})

    if job_id:
        _update_job_status(
            client,
            job_id,
            "retrying",
            error=f"Attempt {retry_count} failed: {error_msg}",
        )
    print(
        f"[*] Job {job.get('module')}.{job.get('function')} failed. "
        f"Scheduled retry #{retry_count} in {delay}s."
    )


def _send_to_dlq(client: Any, job: dict[str, Any], error_msg: str) -> None:
    job_id = job.get("id")
    job["failed_at"] = time.time()
    job["error"] = error_msg

    client.lpush("asok:dlq", json.dumps(job))

    if job_id:
        _update_job_status(client, job_id, "failed", error=error_msg)
    print(
        f"[x] Job {job.get('module')}.{job.get('function')} failed permanently. "
        "Sent to DLQ."
    )


def _serialize_result_safe(result: Any) -> Any:
    try:
        return json.loads(json.dumps(result))
    except Exception:
        return str(result)


def _handle_job_failure(client: Any, job: dict[str, Any], error_msg: str) -> None:
    if job.get("retry_count", 0) < job.get("retries", 0):
        _schedule_job_retry(client, job, error_msg)
    else:
        _send_to_dlq(client, job, error_msg)


def _execute_job_in_thread(client: Any, job_data: bytes) -> None:
    job = _parse_and_verify_job(job_data)
    if not job:
        return

    job_id = job.get("id")
    if job_id:
        _update_job_status(client, job_id, "running")

    module_name = job["module"]
    func_name = job["function"]
    args = job["args"]
    kwargs = job["kwargs"]

    print(f"[+] Processing job: {module_name}.{func_name} ...")
    start_time = time.time()
    try:
        mod = importlib.import_module(module_name)
        func = getattr(mod, func_name)
        result = func(*args, **kwargs)
        elapsed = time.time() - start_time
        print(f"[v] Job {module_name}.{func_name} completed in {elapsed:.3f}s")
        if job_id:
            res_obj = _serialize_result_safe(result)
            _update_job_status(client, job_id, "completed", result=res_obj)
    except Exception as e:
        elapsed = time.time() - start_time
        error_msg = f"{type(e).__name__}: {e}"
        print(
            f"[x] Job {module_name}.{func_name} failed after {elapsed:.3f}s: {error_msg}"
        )
        logger.error("Job execution failed", exc_info=True)
        _handle_job_failure(client, job, error_msg)


def _handle_redis_error(e: Exception, redis_mod: Any) -> None:
    if isinstance(e, redis_mod.exceptions.ConnectionError):
        print(f"[*] Redis connection lost: {e}. Retrying in 5 seconds...")
        time.sleep(5)


def _reschedule_single_task(client: Any, envelope: Any) -> None:
    try:
        envelope_str = (
            envelope.decode("utf-8") if isinstance(envelope, bytes) else envelope
        )
        envelope_dict = json.loads(envelope_str)
        job = json.loads(envelope_dict["job"])
        queue = job.get("queue", "default")
        queue_key = "asok:queue" if queue == "default" else f"asok:queue:{queue}"
        client.lpush(queue_key, envelope)
    except Exception as e:
        logger.error(f"Error rescheduling delayed task: {e}")


def _delayed_tasks_manager_loop(client: Any, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            now = time.time()
            tasks = client.zrangebyscore("asok:delayed_tasks", 0, now)
            for envelope in tasks:
                if client.zrem("asok:delayed_tasks", envelope):
                    _reschedule_single_task(client, envelope)
        except Exception as e:
            logger.error(f"Error in delayed tasks manager: {e}")
        time.sleep(1)


def _parse_queues_list(queues_env: str) -> list[str]:
    queues = []
    for q in queues_env.split(","):
        q = q.strip()
        if q == "default":
            queues.append("asok:queue")
        elif q:
            queues.append(f"asok:queue:{q}")
    return queues


def _handle_worker_loop_exception(e: Exception, redis_mod: Any) -> None:
    class_name = type(e).__name__
    is_redis = class_name in ("TimeoutError", "ConnectionError")
    if is_redis or "redis" in type(e).__module__:
        _handle_redis_error(e, redis_mod)
    else:
        print(f"Error: {e}")
        time.sleep(2)


def _process_one_queue_item(
    client: Any, queues: list[str], executor: Any, redis_mod: Any
) -> None:
    try:
        res = client.brpop(queues, timeout=5)
        if res:
            _, job_data = res
            executor.submit(_execute_job_in_thread, client, job_data)
    except Exception as e:
        _handle_worker_loop_exception(e, redis_mod)


def _run_worker_loop(client: Any, redis_url: str) -> None:
    from concurrent.futures import ThreadPoolExecutor

    import redis

    concurrency = int(os.environ.get("ASOK_WORKER_CONCURRENCY", "1"))
    queues_env = os.environ.get("ASOK_WORKER_QUEUES", "high,default,low")
    queues = _parse_queues_list(queues_env)

    print(
        f"[*] Asok Worker started (concurrency={concurrency}). "
        f"Listening to queues {', '.join(queues)} on {redis_url}..."
    )

    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    stop_event = threading.Event()
    delayed_thread = threading.Thread(
        target=_delayed_tasks_manager_loop, args=(client, stop_event), daemon=True
    )
    delayed_thread.start()

    executor = ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="asok_worker_"
    )

    try:
        while True:
            _process_one_queue_item(client, queues, executor, redis)
    except KeyboardInterrupt:
        print("\n[*] Worker stopping...")
    finally:
        stop_event.set()
        executor.shutdown(wait=True)


def run_worker(action: str = "run") -> None:
    """Run or inspect the background task queue worker."""
    client, redis_url = _init_redis_client()

    if action == "status":
        show_queue_status(client, redis_url)
        return

    _run_worker_loop(client, redis_url)


def _get_queue_len(
    client: Any, Style: Any, queue_name: str = "asok:queue:default"
) -> int:
    try:
        return client.llen(queue_name)
    except Exception as e:
        Style.error(f"Failed to connect to Redis: {e}")
        sys.exit(1)


def _get_raw_jobs(
    client: Any, Style: Any, queue_name: str = "asok:queue:default"
) -> list:
    try:
        return client.lrange(queue_name, 0, -1)
    except Exception as e:
        Style.error(f"Failed to retrieve tasks from Redis: {e}")
        sys.exit(1)


def _format_job_params(args: list, kwargs: dict) -> str:
    arg_str = ", ".join(repr(a) for a in args)
    kwarg_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
    params = []
    if arg_str:
        params.append(arg_str)
    if kwarg_str:
        params.append(kwarg_str)
    return ", ".join(params)


def _print_single_job(i: int, job_bytes: bytes, Style: Any) -> None:
    try:
        envelope = json.loads(
            job_bytes.decode("utf-8") if isinstance(job_bytes, bytes) else job_bytes
        )
        job = json.loads(envelope["job"]) if "job" in envelope else envelope
        module = job.get("module", "unknown")
        func = job.get("function", "unknown")
        args = job.get("args", [])
        kwargs = job.get("kwargs", {})

        params_str = _format_job_params(args, kwargs)
        print(f"    {i:2d}. {Style.CYAN}{module}.{func}{Style.RESET}({params_str})")
    except Exception as e:
        print(
            f"    {i:2d}. {Style.RED}[Invalid Job Data]{Style.RESET}: {e} (Raw: {job_bytes})"
        )


def _print_jobs(raw_jobs: list, Style: Any) -> None:
    jobs_in_order = list(reversed(raw_jobs))
    print(f"    {Style.BOLD}Next tasks to process:{Style.RESET}\n")
    for i, job_bytes in enumerate(jobs_in_order, start=1):
        _print_single_job(i, job_bytes, Style)
    print()


def show_queue_status(client: Any, redis_url: str) -> None:
    """Print nicely formatted status of the Redis queues."""
    from .style import Style

    Style.heading("ASOK QUEUE STATUS")
    print(f"  Backend: {Style.BOLD}redis{Style.RESET}")
    print(f"  Redis URL: {Style.DIM}{redis_url}{Style.RESET}")
    print("-" * 50)

    queues = ["asok:queue:high", "asok:queue:default", "asok:queue:low", "asok:dlq"]
    for q in queues:
        q_len = _get_queue_len(client, Style, q)
        print(
            f"  Queue: {Style.BOLD}{q}{Style.RESET} | Pending tasks: {Style.BOLD}{q_len}{Style.RESET}"
        )
        if q_len > 0:
            raw_jobs = _get_raw_jobs(client, Style, q)
            _print_jobs(raw_jobs, Style)
        else:
            print(f"    {Style.GREEN}✓{Style.RESET} No pending tasks.")
        print()
