from __future__ import annotations

import email.utils
import re
from datetime import datetime
from typing import Any, Callable, Optional


class APIVersionInfo:
    """Stores deprecation and sunset metadata for API versions."""

    def __init__(
        self,
        version: str,
        deprecated: bool = False,
        sunset: Optional[str] = None,
    ):
        self.version = version
        self.deprecated = deprecated
        self.sunset = sunset


def _get_header_val(headers: dict, name: str) -> Optional[str]:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


def _get_path_version(path: str) -> Optional[str]:
    cleaned = path.strip("/")
    if not cleaned:
        return None
    for part in cleaned.split("/"):
        if re.match(r"^v\d+(?:\.\d+)?$", part):
            return part
    return None


def get_request_version(request: Any) -> Optional[str]:
    """Resolve the requested API version from path, headers, or accept content types."""
    v = _get_path_version(request.path)
    if v:
        return v

    v = _get_header_val(request.headers, "x-api-version")
    if v:
        return v.strip().lower()

    accept = _get_header_val(request.headers, "accept") or ""
    match = re.search(r"vnd\.asok\.v?(\d+(?:\.\d+)?)\+json", accept, re.I)
    if match:
        return f"v{match.group(1)}"

    return None


def api_version(
    version: str,
    deprecated: bool = False,
    sunset: Optional[str] = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to annotate controller methods with specific API versions."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._asok_api_version = APIVersionInfo(
            version=version, deprecated=deprecated, sunset=sunset
        )
        return fn

    return decorator


def _apply_sunset_header(request: Any, sunset: str) -> None:
    try:
        dt = datetime.fromisoformat(sunset.replace("Z", "+00:00"))
        http_date = email.utils.format_datetime(dt, usegmt=True)
        request.response_headers.append(("Sunset", http_date))
    except Exception:
        request.response_headers.append(("Sunset", sunset))


def _apply_version_headers(request: Any, handler: Callable[..., Any]) -> None:
    meta = getattr(handler, "_asok_api_version", None)
    if not meta:
        return
    if meta.deprecated:
        request.response_headers.append(("Deprecation", "true"))
    if meta.sunset:
        _apply_sunset_header(request, meta.sunset)


def versioned_response(
    request: Any,
    version_map: dict[str, Callable[..., Any]],
    default: Optional[str] = None,
) -> Any:
    """Inline router to dispatch controller response to matching versioned handlers."""
    req_version = get_request_version(request) or default
    if not req_version or req_version not in version_map:
        if default in version_map:
            req_version = default
        else:
            return request.json({"error": "API Version not supported"}, status=400)

    handler = version_map[req_version]
    res = handler(request)
    _apply_version_headers(request, handler)
    return res
