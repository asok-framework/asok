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
) -> logging.Logger:
    """Create a configured logger instance.

    Args:
        name:        Logger name.
        level:       Logging level (default: from LOG_LEVEL env or DEBUG).
        log_file:    Optional file path (default: from LOG_FILE env).
        json_format: Use structured JSON output (default: from LOG_FORMAT env).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

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
        # SECURITY: Use RotatingFileHandler to prevent log files from growing indefinitely
        # Max 10MB per file, keep 5 backup files (total ~50MB)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            encoding="utf-8",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger


class RequestLogger:
    """Middleware that logs every incoming request and its processing time."""

    def __init__(self, logger_name: str = "asok.request"):
        """Initialize the request logger."""
        self.logger = get_logger(logger_name)

    def __call__(self, request: Request, next_handler: Callable[[Request], Any]) -> Any:
        """Log request details and execution duration."""
        start = time.time()
        try:
            result = next_handler(request)
            elapsed = (time.time() - start) * 1000

            # Neutralize potential log injection (CR/LF) in path/status
            path = request.path.replace("\n", "").replace("\r", "")
            method = request.method.replace("\n", "").replace("\r", "")
            status = request.status.replace("\n", "").replace("\r", "")

            self.logger.info(
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
            self.logger.error(
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
