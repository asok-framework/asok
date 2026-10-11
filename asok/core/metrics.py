from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

if TYPE_CHECKING:
    from .asok import Asok


def _escape_prometheus_label(val: Any) -> str:
    """Escape label values according to Prometheus exposition format."""
    s = str(val or "")
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricsRegistry:
    """Thread-safe Prometheus-compatible metrics registry (Zero external dependencies)."""

    def __init__(self, max_cardinality: int = 1000) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._max_cardinality = max_cardinality
        self._http_requests: Dict[Tuple[str, str, str], int] = {}
        self._http_duration_sum: float = 0.0
        self._http_duration_count: int = 0
        self._db_queries: Dict[str, int] = {}
        self._db_duration_sum: float = 0.0
        self._db_duration_count: int = 0
        self._slow_queries_count: int = 0
        self._ws_active_connections: int = 0

    def _resolve_key(
        self, method: str, status: int | str, path: str
    ) -> Tuple[str, str, str]:
        key = (str(method).upper(), str(status), str(path))
        if key in self._http_requests:
            return key
        if len(self._http_requests) >= self._max_cardinality:
            return (str(method).upper(), str(status), "/other")
        return key

    def record_http_request(
        self, method: str, status: int | str, path: str, duration_sec: float
    ) -> None:
        with self._lock:
            key = self._resolve_key(method, status, path)
            self._http_requests[key] = self._http_requests.get(key, 0) + 1
            self._http_duration_sum += duration_sec
            self._http_duration_count += 1

    def record_db_query(
        self, engine: str, duration_sec: float, is_slow: bool = False
    ) -> None:
        engine_name = str(engine or "sqlite")
        with self._lock:
            self._db_queries[engine_name] = self._db_queries.get(engine_name, 0) + 1
            self._db_duration_sum += duration_sec
            self._db_duration_count += 1
            if is_slow:
                self._slow_queries_count += 1

    def set_ws_connections(self, count: int) -> None:
        with self._lock:
            self._ws_active_connections = max(0, count)

    def inc_ws_connections(self, amount: int = 1) -> None:
        with self._lock:
            self._ws_active_connections = max(0, self._ws_active_connections + amount)

    def dec_ws_connections(self, amount: int = 1) -> None:
        with self._lock:
            self._ws_active_connections = max(0, self._ws_active_connections - amount)

    @property
    def uptime(self) -> float:
        return time.time() - self._start_time

    def export_prometheus(self) -> str:
        with self._lock:
            lines = [
                "# HELP asok_uptime_seconds Application uptime in seconds.",
                "# TYPE asok_uptime_seconds gauge",
                f"asok_uptime_seconds {self.uptime:.2f}",
                "# HELP asok_http_requests_total Total number of HTTP requests processed.",
                "# TYPE asok_http_requests_total counter",
            ]
            for (m, s, p), count in sorted(self._http_requests.items()):
                em, es, ep = (
                    _escape_prometheus_label(m),
                    _escape_prometheus_label(s),
                    _escape_prometheus_label(p),
                )
                lines.append(
                    f'asok_http_requests_total{{method="{em}",status="{es}",path="{ep}"}} {count}'
                )
            lines.extend(self._format_http_duration())
            lines.extend(self._format_db_metrics())
            lines.extend(self._format_ws_metrics())
            return "\n".join(lines) + "\n"

    def _format_http_duration(self) -> list[str]:
        return [
            "# HELP asok_http_request_duration_seconds Latency of HTTP requests in seconds.",
            "# TYPE asok_http_request_duration_seconds summary",
            f"asok_http_request_duration_seconds_count {self._http_duration_count}",
            f"asok_http_request_duration_seconds_sum {self._http_duration_sum:.4f}",
        ]

    def _format_db_metrics(self) -> list[str]:
        lines = [
            "# HELP asok_db_queries_total Total number of database queries executed.",
            "# TYPE asok_db_queries_total counter",
        ]
        for engine, count in sorted(self._db_queries.items()):
            ee = _escape_prometheus_label(engine)
            lines.append(f'asok_db_queries_total{{engine="{ee}"}} {count}')
        lines.extend(
            [
                "# HELP asok_db_query_duration_seconds Total time spent in database queries.",
                "# TYPE asok_db_query_duration_seconds summary",
                f"asok_db_query_duration_seconds_count {self._db_duration_count}",
                f"asok_db_query_duration_seconds_sum {self._db_duration_sum:.4f}",
                "# HELP asok_db_slow_queries_total Total number of slow queries detected.",
                "# TYPE asok_db_slow_queries_total counter",
                f"asok_db_slow_queries_total {self._slow_queries_count}",
            ]
        )
        return lines

    def _format_ws_metrics(self) -> list[str]:
        return [
            "# HELP asok_websocket_active_connections Number of active WebSocket connections.",
            "# TYPE asok_websocket_active_connections gauge",
            f"asok_websocket_active_connections {self._ws_active_connections}",
        ]


default_metrics = MetricsRegistry()


def _check_db_health(app: Optional[Asok]) -> str:
    if not app:
        return "skipped"
    try:
        from ..orm.model import Model

        engine = Model.get_engine()
        if engine:
            engine.execute("SELECT 1;")
            return "ok"
    except Exception as e:
        import logging

        logging.getLogger("asok.metrics").warning("Database health check failed: %s", e)
        if getattr(app, "debug", False):
            return f"error: {e}"
        return "error: database unavailable"
    return "ok"


def _check_cache_health(app: Optional[Asok]) -> str:
    if not app:
        return "skipped"
    try:
        from ..cache import default_cache

        if default_cache:
            default_cache.set("__healthcheck__", "1", ttl=10)
            val = default_cache.get("__healthcheck__")
            if val == "1":
                return "ok"
    except Exception:
        return "degraded"
    return "ok"


def _resolve_app_version(app: Optional[Asok]) -> str:
    if app and hasattr(app, "version"):
        return str(app.version)
    try:
        from .. import __version__

        return str(__version__)
    except Exception:
        return "0.8.0"


def check_health(app: Optional[Asok] = None) -> tuple[dict[str, Any], int]:
    """Perform health checks on database and cache and return health JSON and status code."""
    db_status = _check_db_health(app)
    cache_status = _check_cache_health(app)
    is_healthy = db_status in ("ok", "skipped")
    status_str = "healthy" if is_healthy else "unhealthy"
    status_code = 200 if is_healthy else 503

    report = {
        "status": status_str,
        "version": _resolve_app_version(app),
        "uptime_seconds": round(default_metrics.uptime, 2),
        "checks": {
            "database": db_status,
            "cache": cache_status,
        },
    }
    return report, status_code
