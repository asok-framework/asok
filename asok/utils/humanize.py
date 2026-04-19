from __future__ import annotations

import datetime
import math
from typing import Any, Union


def time_ago(dt: Union[datetime.datetime, str, None]) -> str:
    """Convert a datetime object or ISO string to a relative time string.

    Args:
        dt: The datetime object or ISO-formatted string to humanize.

    Returns:
        A relative string like "3 minutes ago" or "2 days ago".
    """
    if not dt:
        return ""
    if isinstance(dt, str):
        try:
            dt = datetime.datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt

    now = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 0:
        return "in the future"
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if seconds < 604800:
        days = int(seconds / 86400)
        return f"{days} day{'s' if days > 1 else ''} ago"
    if seconds < 2592000:
        weeks = int(seconds / 604800)
        return f"{weeks} week{'s' if weeks > 1 else ''} ago"
    if seconds < 31536000:
        months = int(seconds / 2592000)
        return f"{months} month{'s' if months > 1 else ''} ago"

    years = int(seconds / 31536000)
    return f"{years} year{'s' if years > 1 else ''} ago"


def file_size(size_bytes: int) -> str:
    """Convert bytes to a human-readable string (e.g. 1.2 MB).

    Args:
        size_bytes: The number of bytes to format.

    Returns:
        A formatted string with the appropriate unit (KB, MB, etc.).
    """
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


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
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")

    return " ".join(parts)
