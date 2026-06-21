from __future__ import annotations

import datetime
import math
from typing import Any, Union


def _parse_datetime(dt: Union[datetime.datetime, str, None]) -> datetime.datetime:
    if not dt:
        raise ValueError("Invalid datetime")
    if isinstance(dt, str):
        if len(dt) > 100:
            raise ValueError("DoS protection: date string too long")
        return datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
    return dt

def _get_seconds_diff(dt: datetime.datetime) -> float:
    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    diff = now - dt
    return diff.total_seconds()

def _get_plural_suffix(count: int) -> str:
    if count > 1:
        return "s"
    return ""

def _format_time_interval(seconds: float) -> str:
    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"

    intervals = [
        (31536000, "year"),
        (2592000, "month"),
        (604800, "week"),
        (86400, "day"),
        (3600, "hour"),
        (60, "minute"),
    ]
    for limit, unit in intervals:
        if seconds >= limit:
            count = int(seconds / limit)
            suffix = _get_plural_suffix(count)
            return f"{count} {unit}{suffix} ago"
    return "just now"

def _fallback_value(dt: Any) -> str:
    if isinstance(dt, str):
        return dt
    return ""

def time_ago(dt: Union[datetime.datetime, str, None]) -> str:
    """Convert a datetime object or ISO string to a relative time string.

    Args:
        dt: The datetime object or ISO-formatted string to humanize.

    Returns:
        A relative string like "3 minutes ago" or "2 days ago".

    SECURITY: Handles extreme dates to prevent errors.
    """
    if not dt:
        return ""
    try:
        parsed_dt = _parse_datetime(dt)
        seconds = _get_seconds_diff(parsed_dt)
    except (ValueError, TypeError, OverflowError, AttributeError):
        return _fallback_value(dt)

    if abs(seconds) > 3_155_760_000:  # > 100 years
        if seconds > 0:
            return "a very long time ago"
        return "in the distant future"

    return _format_time_interval(seconds)


def file_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string (e.g. 1.2 MB).

    Args:
        size_bytes: The number of bytes to format.

    Returns:
        A formatted string with the appropriate unit (KB, MB, etc.).

    SECURITY: Handles edge cases to prevent errors with invalid inputs.
    """
    # SECURITY: Handle negative and invalid values
    if size_bytes < 0:
        return "0 B"
    if size_bytes == 0:
        return "0 B"

    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    try:
        i = int(math.floor(math.log(size_bytes, 1024)))
        # SECURITY: Prevent index out of bounds
        if i >= len(size_name):
            i = len(size_name) - 1
        p = math.pow(1024, i)
        s = round(size_bytes / p, 2)
        return f"{s} {size_name[i]}"
    except (ValueError, OverflowError):
        return f"{size_bytes} B"


def intcomma(value: Union[int, float, str]) -> Union[str, Any]:
    """Add thousands separators to an integer or float.

    Args:
        value: The number to format.

    Returns:
        A comma-separated string representation.
    """
    try:
        if isinstance(value, str):
            value = float(value)
        return f"{value:,}"
    except (ValueError, TypeError):
        return value


def _add_duration_part(parts: list[str], value: int, suffix: str) -> None:
    if value > 0:
        parts.append(f"{value}{suffix}")


def duration(seconds: Union[int, float, None]) -> str:
    """Convert seconds to a human-readable duration (e.g. 5m 20s).

    Args:
        seconds: Total seconds to format.

    Returns:
        A succinct duration string.
    """
    if not seconds:
        return "0s"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)

    parts = []
    _add_duration_part(parts, days, "d")
    _add_duration_part(parts, hours, "h")
    _add_duration_part(parts, minutes, "m")

    if seconds > 0:
        parts.append(f"{seconds}s")
    if not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)
