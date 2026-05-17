from __future__ import annotations

import json as _json
import logging
import logging.handlers
import os
import time
from typing import Any, Callable, Optional

from .request import Request


class _JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production environments."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            entry["exception"] = str(record.exc_info[1])
        # Merge any extra structured fields
        for key in ("method", "path", "status", "duration_ms", "ip"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        return _json.dumps(entry)


def get_logger(
    name: str = "asok",
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    json_format: Optional[bool] = None,
    config: Optional[dict[str, Any]] = None,
) -> logging.Logger:
    """Create a configured logger instance.

    Args:
        name:        Logger name.
        level:       Logging level (prioritizes arg > config > env).
        log_file:    Optional file path (prioritizes arg > config > env).
        json_format: Use structured JSON output (prioritizes arg > config > env).
        config:      Optional application config dictionary.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    # Resolve configuration (Priority: Argument > App Config > Environment > Default)
    if config:
        level = level or config.get("LOG_LEVEL")
        log_file = log_file or config.get("LOG_FILE")
        if json_format is None:
            json_format = config.get("LOG_FORMAT", "").lower() == "json"

    level = level or os.environ.get("LOG_LEVEL", "DEBUG")
    log_file = log_file or os.environ.get("LOG_FILE")
    if json_format is None:
        json_format = os.environ.get("LOG_FORMAT", "").lower() == "json"

    logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    if json_format:
        fmt = _JSONFormatter()
    else:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_file:
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
            # Fallback to console-only if file is not writable
            logger.error(f"Could not initialize log file {log_file}: {e}")

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
