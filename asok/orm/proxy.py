from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("asok.orm")


def _resolve_slow_threshold(req: Any) -> float:
    app_ref = getattr(req, "environ", {}).get("asok.app") if req else None
    if app_ref and hasattr(app_ref, "config"):
        return float(app_ref.config.get("SLOW_QUERY_THRESHOLD_MS", 100))
    return 100.0


def record_query_telemetry(
    engine_name: str, req: Any, sql: str, parameters: Any, duration_ms: float
) -> None:
    """Record query duration into metrics and log warnings for slow queries."""
    threshold = _resolve_slow_threshold(req)
    is_slow = duration_ms >= threshold
    if is_slow:
        logger.warning(
            "[SLOW QUERY] %.2fms (threshold: %.0fms) | SQL: %s | Params: %s",
            duration_ms,
            threshold,
            sql,
            parameters,
        )
    try:
        from ..core.metrics import default_metrics

        default_metrics.record_db_query(
            engine_name, duration_ms / 1000.0, is_slow=is_slow
        )
    except Exception:
        pass


def _append_to_sql_log(req: Any, entry: dict[str, Any]) -> None:
    if not hasattr(req, "_asok_sql_log"):
        req._asok_sql_log = []
    req._asok_sql_log.append(entry)


def log_and_record_query(
    engine: str, req: Any, sql: str, args: Any, start: float
) -> None:
    """Helper to record query duration into request SQL log and Prometheus metrics."""
    duration = (time.time() - start) * 1000 if start > 0 else 0.0
    if req is not None:
        _append_to_sql_log(
            req, {"sql": sql, "params": args or (), "duration": duration}
        )
    record_query_telemetry(engine, req, sql, args or (), duration)


class ConnectionProxy:
    """A wrapper for database connections that logs query execution and detects slow queries."""

    def __init__(self, conn, engine_name: str = "sqlite"):
        self._conn = conn
        self._engine_name = engine_name

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        """Execute a SQL statement, log performance, and detect slow queries."""
        from ..context import request_var

        req = request_var.get()
        start = time.time()
        try:
            return self._conn.execute(sql, parameters)
        finally:
            log_and_record_query(self._engine_name, req, sql, parameters, start)

    def executemany(self, sql: str, seq_of_parameters: Any) -> Any:
        """Execute multiple SQL statements, log performance, and detect slow queries."""
        from ..context import request_var

        req = request_var.get()
        start = time.time()
        try:
            return self._conn.executemany(sql, seq_of_parameters)
        finally:
            count = (
                len(seq_of_parameters) if hasattr(seq_of_parameters, "__len__") else "?"
            )
            summary = f"[{count} rows]"
            log_and_record_query(self._engine_name, req, sql, summary, start)

    def __enter__(self) -> ConnectionProxy:
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return self._conn.__exit__(exc_type, exc_val, exc_tb)
