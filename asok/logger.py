"""
Structured logging system for the Asok framework.

Provides standard log formatters, request tracing attachments,
and production-ready JSON log formatters.
"""

from __future__ import annotations

import json as _json
import logging
import logging.handlers
import os
import time
from typing import Any, Callable, Optional

from .request import Request

_STANDARD_LOG_RECORD_ATTRS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production environments and SIEM integration."""

    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        """Format timestamp as ISO-8601 UTC string."""
        import datetime

        dt = datetime.datetime.fromtimestamp(record.created, datetime.timezone.utc)
        return dt.strftime(datefmt or "%Y-%m-%dT%H:%M:%SZ")

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a structured JSON string."""
        entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        self._add_exc_info(record, entry)
        self._add_extra_fields(record, entry)
        return _json.dumps(entry, default=str)

    def _add_exc_info(self, record: logging.LogRecord, entry: dict[str, Any]) -> None:
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        if record.stack_info:
            entry["stack_info"] = str(record.stack_info)

    def _add_http_fields(
        self, record: logging.LogRecord, entry: dict[str, Any]
    ) -> None:
        for key in ("method", "path", "status", "duration_ms", "ip"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val

    def _safe_serialize_val(self, val: Any) -> Any:
        try:
            _json.dumps(val)
            return val
        except (TypeError, ValueError):
            return str(val)

    def _add_custom_extras(
        self, record: logging.LogRecord, entry: dict[str, Any]
    ) -> None:
        for key, val in record.__dict__.items():
            if (
                key not in _STANDARD_LOG_RECORD_ATTRS
                and not key.startswith("_")
                and key not in entry
            ):
                entry[key] = self._safe_serialize_val(val)

    def _add_extra_fields(
        self, record: logging.LogRecord, entry: dict[str, Any]
    ) -> None:
        self._add_http_fields(record, entry)
        self._add_custom_extras(record, entry)


# Backward compatibility alias
_JSONFormatter = JSONFormatter


def _resolve_level(level: Optional[str], cfg: dict, env: Any) -> str:
    if level is not None:
        return level
    return cfg.get("LOG_LEVEL") or env.get("LOG_LEVEL", "DEBUG")


def _resolve_log_file(log_file: Optional[str], cfg: dict, env: Any) -> Optional[str]:
    if log_file is not None:
        return log_file
    return cfg.get("LOG_FILE") or env.get("LOG_FILE")


def _resolve_json_format(json_format: Optional[bool], cfg: dict, env: Any) -> bool:
    if json_format is not None:
        return json_format
    fmt = (
        cfg.get("LOG_FORMAT") or env.get("ASOK_LOG_FORMAT") or env.get("LOG_FORMAT", "")
    )
    return fmt.lower() == "json"


def _resolve_log_config(
    level: Optional[str],
    log_file: Optional[str],
    json_format: Optional[bool],
    config: Optional[dict[str, Any]],
) -> tuple[str, Optional[str], bool]:
    cfg = config or {}
    env = os.environ
    return (
        _resolve_level(level, cfg, env),
        _resolve_log_file(log_file, cfg, env),
        _resolve_json_format(json_format, cfg, env),
    )


def _add_rotating_file_handler(
    logger: logging.Logger, log_file: str, fmt: logging.Formatter
) -> None:
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            encoding="utf-8",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except Exception as e:
        logger.error(f"Could not initialize log file {log_file}: {e}")


def configure_security_logger(
    json_format: bool = True,
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    stream: Optional[Any] = None,
) -> logging.Logger:
    """Configure the 'asok.security' logger with structured formatting (JSON or text).

    Args:
        json_format: Use structured JSON formatting (ideal for SIEM / audit collection).
        level: Log level (e.g. 'DEBUG', 'INFO', 'WARNING'). Defaults to 'INFO'.
        log_file: Optional rotating file path for security events.
        stream: Optional stream for console output (defaults to sys.stderr).

    Returns:
        The configured 'asok.security' logging.Logger instance.
    """
    logger = logging.getLogger("asok.security")
    logger.handlers.clear()

    level_str = (
        level
        or os.environ.get("ASOK_SECURITY_LOG_LEVEL")
        or os.environ.get("SECURITY_LOG_LEVEL", "INFO")
    )
    logger.setLevel(getattr(logging, level_str.upper(), logging.INFO))
    logger.propagate = False

    fmt: logging.Formatter
    if json_format:
        fmt = JSONFormatter()
    else:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console = logging.StreamHandler(stream)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
        _add_rotating_file_handler(logger, log_file, fmt)

    return logger


def _prepare_logger_handlers(logger: logging.Logger, reconfigure: bool) -> bool:
    if logger.handlers and not reconfigure:
        return True
    if reconfigure:
        logger.handlers.clear()
    return False


def _build_formatter(is_json: bool) -> logging.Formatter:
    if is_json:
        return JSONFormatter()
    return logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def get_logger(
    name: str = "asok",
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    json_format: Optional[bool] = None,
    config: Optional[dict[str, Any]] = None,
    reconfigure: bool = False,
) -> logging.Logger:
    """Create a configured logger instance.

    Args:
        name:        Logger name.
        level:       Logging level (prioritizes arg > config > env).
        log_file:    Optional file path (prioritizes arg > config > env).
        json_format: Use structured JSON output (prioritizes arg > config > env).
        config:      Optional application config dictionary.
        reconfigure: Reconfigure existing logger handlers if True.
    """
    logger = logging.getLogger(name)
    if _prepare_logger_handlers(logger, reconfigure):
        return logger

    level_str, file_path, is_json = _resolve_log_config(
        level, log_file, json_format, config
    )
    logger.setLevel(getattr(logging, level_str.upper(), logging.DEBUG))
    fmt = _build_formatter(is_json)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    if file_path:
        _add_rotating_file_handler(logger, file_path, fmt)

    return logger


class RequestLogger:
    """Middleware that logs every incoming request and its processing time."""

    def __init__(self, logger_name: str = "asok.request"):
        """Initialize the request logger."""
        self.logger = get_logger(logger_name)

    def __call__(self, request: Request, next_handler: Callable[[Request], Any]) -> Any:
        """Log request details and execution duration."""
        start = time.time()

        # Use application logger if available
        app = request.environ.get("asok.app")
        logger = getattr(app, "logger", self.logger)

        try:
            result = next_handler(request)
            elapsed = (time.time() - start) * 1000

            # Neutralize potential log injection (CR/LF) in path/status
            path = request.path.replace("\n", "").replace("\r", "")
            method = request.method.replace("\n", "").replace("\r", "")
            status = request.status.replace("\n", "").replace("\r", "")

            logger.info(
                "%s %s %s %.1fms",
                method,
                path,
                status,
                elapsed,
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round(elapsed, 1),
                    "ip": request.ip,
                },
            )
            return result
        except Exception as e:
            elapsed = (time.time() - start) * 1000
            logger.error(
                "%s %s 500 %.1fms — %s",
                request.method,
                request.path,
                elapsed,
                str(e),
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": "500 Internal Server Error",
                    "duration_ms": round(elapsed, 1),
                    "ip": request.ip,
                },
            )
            raise
