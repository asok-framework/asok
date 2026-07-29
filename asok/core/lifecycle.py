from __future__ import annotations

import inspect
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger("asok.lifecycle")


class LifecycleMixin:
    """Mixin class for Asok that manages the application lifecycle, middleware registration,
    background threads, scheduled tasks, rate limiting, and caching decorators.
    """

    def use(self, middleware: Callable, priority: int = 50) -> Any:
        """Register a middleware handler programmatically with optional priority."""
        self.middleware_handlers.append(middleware)
        if not hasattr(middleware, "_asok_priority"):
            middleware._asok_priority = priority
        self.middleware_handlers.sort(key=lambda m: getattr(m, "_asok_priority", 50))
        self._middleware_chain = None
        self._has_async_middleware = any(
            inspect.iscoroutinefunction(mw) for mw in self.middleware_handlers
        )
        self._middleware_handlers_reversed = list(reversed(self.middleware_handlers))
        return self

    def on_startup(self, fn: Callable) -> Callable:
        """Register a function to be called when the app starts up."""
        self._on_startup.append(fn)
        return fn

    def on_shutdown(self, fn: Callable) -> Callable:
        """Register a function to be called when the app shuts down."""
        self._on_shutdown.append(fn)
        return fn

    def _run_async_hook_safe(self, fn: Callable) -> None:
        try:
            res = fn()
            if inspect.iscoroutine(res):
                self._handle_coroutine_res(res)
        except Exception as e:
            logger.warning("Could not execute async startup hook: %s", e)

    def _handle_coroutine_res(self, coro: Any) -> None:
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                asyncio.run(coro)
        except Exception:
            coro.close()

    def startup(self) -> None:
        """Run all registered startup hooks."""
        from ..backup import BackupManager
        from .signals import app_startup

        app_startup.send(self)
        for fn in self._on_startup:
            if inspect.iscoroutinefunction(fn):
                self._run_async_hook_safe(fn)
            else:
                res = fn()
                if inspect.iscoroutine(res):
                    self._handle_coroutine_res(res)

        try:
            BackupManager.start_scheduled_backups(self)
        except Exception as e:
            logger.warning("Could not start background backup scheduler: %s", e)

    def shutdown(self) -> None:
        """Run all registered shutdown hooks and stop background tasks."""
        from .signals import app_shutdown

        app_shutdown.send(self)
        for fn in self._on_shutdown:
            fn()

        self._cancel_background_tasks()
        self._shutdown_executor()
        self._stop_session_cleanup()

    def _cancel_background_tasks(self) -> None:
        for task in self._tasks:
            try:
                task.cancel()
            except Exception:
                pass

    def _shutdown_executor(self) -> None:
        if self._executor:
            try:
                self._executor.shutdown(wait=False)
            except Exception:
                pass

    def _stop_session_cleanup(self) -> None:
        if hasattr(self, "_session_store"):
            self._session_store.stop_cleanup_timer()

    def share(self, **kwargs: Any) -> Any:
        self._shared.update(kwargs)
        return self

    def schedule(
        self,
        interval: str | float,
        fn: Optional[Callable] = None,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Schedule a recurring background task managed by the app."""
        from ..scheduler import schedule as _schedule

        def decorator(func: Callable) -> Any:
            task = _schedule(interval, func, *args, **kwargs)
            self._tasks.append(task)
            return task

        if fn is None:
            return decorator

        task = _schedule(interval, fn, *args, **kwargs)
        self._tasks.append(task)
        return task

    def log_info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.info(message, *args, **kwargs)

    def log_error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.error(message, *args, **kwargs)

    def background(
        self, fn: Optional[Callable] = None, *args: Any, **kwargs: Any
    ) -> Any:
        """Run a function in a background thread managed by the app."""
        from ..background import background as _background

        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self.config.get("BG_WORKERS", 10),
                thread_name_prefix=f"asok_app_{id(self)}_",
            )

        def decorator(func: Callable) -> Callable:
            @wraps(func)
            def wrapper(*fargs, **fkwargs):
                return _background(func, *fargs, executor=self._executor, **fkwargs)

            return wrapper

        if fn is None:
            return decorator

        if args or kwargs:
            return _background(fn, *args, executor=self._executor, **kwargs)

        return decorator(fn)

    def rate_limit(
        self, limit: str | int, window: Optional[int] = None, **kwargs: Any
    ) -> Callable:
        from ..ratelimit import rate_limit as _rate_limit

        return _rate_limit(limit, window, **kwargs)

    def cache_page(self, ttl: int = 60, key_prefix: str = "page_") -> Callable:
        from ..cache import cache_page as _cache_page

        return _cache_page(ttl, key_prefix)
