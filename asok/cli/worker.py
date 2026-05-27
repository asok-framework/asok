from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time

logger = logging.getLogger("asok.worker")


def run_worker() -> None:
    """Run the background task queue worker."""
    backend = os.environ.get("ASOK_QUEUE_BACKEND", "local").lower()
    if backend != "redis":
        print("Error: ASOK_QUEUE_BACKEND must be set to 'redis' to run a worker.")
        sys.exit(1)

    try:
        import redis
    except ImportError:
        print("Error: The 'redis' package is required. Run 'pip install asok[redis]'.")
        sys.exit(1)

    redis_url = os.environ.get("ASOK_REDIS_URL") or os.environ.get("REDIS_URL") or "redis://localhost:6379/0"
    client = redis.Redis.from_url(redis_url)

    print(f"[*] Asok Worker started. Listening to Redis queue 'asok:queue' on {redis_url}...")

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
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(2)
