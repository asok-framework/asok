from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from typing import Any

logger = logging.getLogger("asok.worker")


def run_worker(action: str = "run") -> None:
    """Run or inspect the background task queue worker."""
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

    redis_url = (
        os.environ.get("ASOK_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or "redis://localhost:6379/0"
    )

    try:
        client = redis.Redis.from_url(redis_url)
    except Exception as e:
        print(f"Error connecting to Redis: {e}")
        sys.exit(1)

    if action == "status":
        show_queue_status(client, redis_url)
        return

    print(
        f"[*] Asok Worker started. Listening to Redis queue 'asok:queue' on {redis_url}..."
    )

    # Enable project paths
    cwd = os.getcwd()
    if cwd not in sys.path:
        sys.path.insert(0, cwd)

    while True:
        try:
            # BRPOP blocks until a job is available
            res = client.brpop("asok:queue", timeout=5)
            if not res:
                continue

            _, job_data = res
            job = json.loads(job_data.decode("utf-8"))

            module_name = job["module"]
            func_name = job["function"]
            args = job["args"]
            kwargs = job["kwargs"]

            print(f"[+] Processing job: {module_name}.{func_name} ...")
            start_time = time.time()

            try:
                mod = importlib.import_module(module_name)
                func = getattr(mod, func_name)
                func(*args, **kwargs)
                elapsed = time.time() - start_time
                print(f"[v] Job {module_name}.{func_name} completed in {elapsed:.3f}s")
            except Exception as e:
                print(f"[x] Job {module_name}.{func_name} failed: {e}")
                logger.error(f"Job execution failed: {e}", exc_info=True)

        except KeyboardInterrupt:
            print("\n[*] Worker stopping...")
            break
        except (redis.exceptions.TimeoutError, redis.exceptions.ConnectionError) as e:
            if isinstance(e, redis.exceptions.ConnectionError):
                print(f"[*] Redis connection lost: {e}. Retrying in 5 seconds...")
                time.sleep(5)
            else:
                # TimeoutError is a normal socket timeout during BRPOP blocking read
                continue
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)


def show_queue_status(client: Any, redis_url: str) -> None:
    """Print nicely formatted status of the Redis queue."""
    from .style import Style

    Style.heading("ASOK QUEUE STATUS")
    print(f"  Backend: {Style.BOLD}redis{Style.RESET}")
    print(f"  Redis URL: {Style.DIM}{redis_url}{Style.RESET}")

    try:
        queue_len = client.llen("asok:queue")
    except Exception as e:
        Style.error(f"Failed to connect to Redis: {e}")
        sys.exit(1)

    print(f"  Pending tasks: {Style.BOLD}{queue_len}{Style.RESET}")
    print("-" * 50)

    if queue_len == 0:
        print(f"  {Style.GREEN}✓{Style.RESET} No pending tasks in queue.")
        return

    try:
        raw_jobs = client.lrange("asok:queue", 0, -1)
    except Exception as e:
        Style.error(f"Failed to retrieve tasks from Redis: {e}")
        sys.exit(1)

    # Reverse the list so the next task to process (at index -1) is shown first
    jobs_in_order = list(reversed(raw_jobs))

    print(f"  {Style.BOLD}Next tasks to process:{Style.RESET}\n")
    for i, job_bytes in enumerate(jobs_in_order, start=1):
        try:
            job = json.loads(job_bytes.decode("utf-8"))
            module = job.get("module", "unknown")
            func = job.get("function", "unknown")
            args = job.get("args", [])
            kwargs = job.get("kwargs", {})

            # Format arguments nicely
            arg_str = ", ".join(repr(a) for a in args)
            kwarg_str = ", ".join(f"{k}={repr(v)}" for k, v in kwargs.items())
            params = []
            if arg_str:
                params.append(arg_str)
            if kwarg_str:
                params.append(kwarg_str)
            params_str = ", ".join(params)

            print(f"  {i:2d}. {Style.CYAN}{module}.{func}{Style.RESET}({params_str})")
        except Exception as e:
            print(
                f"  {i:2d}. {Style.RED}[Invalid Job Data]{Style.RESET}: {e} (Raw: {job_bytes})"
            )

    print()
