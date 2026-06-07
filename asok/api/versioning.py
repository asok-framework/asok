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


def get_request_version(request: Any) -> Optional[str]:
    """Resolve the requested API version from path, headers, or accept content types."""
    # 1. URL Path check (e.g. /api/v1/users)
    path = request.path.strip("/")
    if path:
        for part in path.split("/"):
            if re.match(r"^v\d+(?:\.\d+)?$", part):
                return part

    # 2. X-API-Version header (case-insensitive)
    for k, v in request.headers.items():
        if k.lower() == "x-api-version":
            return v.strip().lower()

    # 3. Accept header (case-insensitive)
    accept = ""
    for k, v in request.headers.items():
        if k.lower() == "accept":
            accept = v
            break

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

    meta = getattr(handler, "_asok_api_version", None)
    if meta:
        if meta.deprecated:
            request.response_headers.append(("Deprecation", "true"))
        if meta.sunset:
            try:
                dt = datetime.fromisoformat(meta.sunset.replace("Z", "+00:00"))
                http_date = email.utils.format_datetime(dt, usegmt=True)
                request.response_headers.append(("Sunset", http_date))
            except Exception:
                request.response_headers.append(("Sunset", meta.sunset))

    return res
