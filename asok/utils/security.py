import os
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import urlsplit

_RE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]")


def _truncate_filename(filename: str) -> str:
    if len(filename) > 255:
        # Keep extension if present
        name, ext = os.path.splitext(filename)
        if ext:
            return name[: 250 - len(ext)] + ext
        return filename[:255]
    return filename


def secure_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other security issues.

    - Removes directory separators.
    - Converts to ASCII.
    - Keeps only alphanumeric, dots, dashes, and underscores.
    - Removes leading/trailing dots and spaces.
    - Limits length to prevent filesystem issues.

    SECURITY: Length limits prevent DoS and filesystem errors.
    """
    if not filename:
        return "unnamed_file"

    # SECURITY: Limit filename length to prevent DoS (max 255 chars, typical filesystem limit)
    filename = _truncate_filename(filename)

    # 1. Normalize and convert to ASCII
    filename = (
        unicodedata.normalize("NFKD", filename)
        .encode("ascii", "ignore")
        .decode("ascii")
    )

    # 2. Replace separators and remove unwanted characters
    filename = filename.replace("/", "_").replace("\\", "_")
    filename = _RE_FILENAME.sub("_", filename)

    # 3. Collapse multiple underscores and strip leading/trailing junk
    filename = re.sub(r"_+", "_", filename)
    filename = filename.strip(" ._")

    if not filename:
        return "unnamed_file"

    # SECURITY: Final length check after sanitization
    filename = _truncate_filename(filename)

    return filename


def _is_safe_absolute_url(url: str, allowed_host: Optional[str]) -> bool:
    if not allowed_host:
        return False
    from urllib.parse import urlparse

    parsed = urlparse(url)

    # Validate scheme (only http/https allowed)
    if parsed.scheme not in ("http", "https"):
        return False

    # Compare the full authority, including the port when present.
    # This prevents redirects to the same host on a different port.
    return parsed.netloc == allowed_host


def _is_safe_relative_url(url: str) -> bool:
    return (
        url.startswith("/") and not url.startswith("//") and not url.startswith("/\\")
    )


def _has_control_chars(url: str) -> bool:
    # Block control characters (CR, LF, tab, null)
    return any(c in url for c in ("\r", "\n", "\t", "\x00"))


def is_safe_url(url: str, allowed_host: Optional[str] = None) -> bool:
    """Check if a URL is safe for redirection (relative or matching current host).

    Blocks protocol-relative URLs (``//``), backslash tricks (``/\\``),
    and control characters that could confuse browser URL parsers.
    """
    if not url or not isinstance(url, str):
        return False

    if _has_control_chars(url):
        return False

    # Absolute URL check
    if "://" in url:
        return _is_safe_absolute_url(url, allowed_host)

    # Relative URL check
    return _is_safe_relative_url(url)


def _request_app_url(request) -> str:
    app = request.environ.get("asok.app")
    if not app:
        return ""
    return (app.config.get("APP_URL") or "").strip()


def _parse_request_host(request) -> str:
    raw_host = request.environ.get("HTTP_HOST") or request.host
    try:
        parsed = urlsplit(f"//{raw_host}", scheme=request.scheme)
    except Exception:
        return request.host

    if not _is_valid_request_host(parsed):
        return request.host
    return parsed.netloc


def _is_valid_request_host(parsed) -> bool:
    return _has_request_hostname(parsed) and not _has_invalid_request_host_parts(parsed)


def _has_request_hostname(parsed) -> bool:
    return bool(parsed.hostname)


def _has_invalid_request_host_parts(parsed) -> bool:
    return _has_request_credentials(parsed) or _has_request_path_parts(parsed)


def _has_request_credentials(parsed) -> bool:
    return bool(parsed.username or parsed.password)


def _has_request_path_parts(parsed) -> bool:
    return bool(parsed.path or parsed.query or parsed.fragment)


def request_authority(request) -> str:
    """Return the canonical authority used for same-origin checks."""
    app_url = _request_app_url(request)
    if app_url:
        parsed_app = urlsplit(app_url)
        if parsed_app.netloc:
            return parsed_app.netloc
    return _parse_request_host(request)


def _is_direct_host_match(netloc: str, request) -> bool:
    host = request.headers.get("Host", "")
    req_host = getattr(request, "host", "")
    return netloc in (host, req_host) if netloc else False


def _extract_app_ref(request):
    environ = getattr(request, "environ", {})
    return getattr(request, "app", None) or (
        environ.get("asok.app") if isinstance(environ, dict) else None
    )


def _is_allowed_origin(netloc: str, request, is_internal_check: bool = True) -> bool:
    if not netloc:
        return False
    if _is_direct_host_match(netloc, request):
        return True
    app_ref = _extract_app_ref(request)
    return (
        _check_app_allowed_origins(netloc, app_ref, is_internal_check=is_internal_check)
        if app_ref
        else False
    )


def _check_app_allowed_origins(
    netloc: str, app_ref, is_internal_check: bool = True
) -> bool:
    if _check_cors_origins(netloc, app_ref, is_internal_check=is_internal_check):
        return True
    if _check_app_url(netloc, app_ref):
        return True
    return _check_allowed_hosts(netloc, app_ref, is_internal_check=is_internal_check)


def _matches_cors_item(netloc: str, cors: str, is_internal_check: bool = False) -> bool:
    if cors == "*":
        return not is_internal_check
    target = cors if "://" in cors else f"http://{cors}"
    return urlsplit(target).netloc == netloc


def _check_cors_origins(netloc: str, app_ref, is_internal_check: bool = False) -> bool:
    origins = app_ref.config.get("CORS_ORIGINS", [])
    cors_list = [origins] if isinstance(origins, str) else origins
    return any(
        _matches_cors_item(netloc, c, is_internal_check=is_internal_check)
        for c in cors_list
    )


def _check_app_url(netloc: str, app_ref) -> bool:
    app_url = app_ref.config.get("APP_URL")
    if not app_url:
        return False
    parsed_app = urlsplit(app_url if "://" in app_url else f"http://{app_url}")
    return parsed_app.netloc == netloc


def _matches_allowed_host(
    host_item: str, netloc: str, host_name: str, is_internal_check: bool
) -> bool:
    if host_item == "*" and is_internal_check:
        return False
    return host_item in ("*", netloc, host_name)


def _check_allowed_hosts(netloc: str, app_ref, is_internal_check: bool = True) -> bool:
    allowed_hosts = app_ref.config.get("ALLOWED_HOSTS", [])
    hosts_list = [allowed_hosts] if isinstance(allowed_hosts, str) else allowed_hosts
    host_name = netloc.split(":")[0]
    return any(
        _matches_allowed_host(h, netloc, host_name, is_internal_check)
        for h in hosts_list
    )


def _is_direct_navigation(request) -> bool:
    sec_mode = request.headers.get("Sec-Fetch-Mode", "").lower()
    sec_dest = request.headers.get("Sec-Fetch-Dest", "").lower()
    accept = request.headers.get("Accept", "").lower()
    if sec_mode == "navigate" or sec_dest == "document":
        return True
    return bool(
        "text/html" in accept
        and "application/json" not in accept
        and "Sec-Fetch-Mode" not in request.headers
    )


def _is_sec_fetch_site_internal(sec_site: str, origin: str, request) -> Optional[bool]:
    if sec_site in ("none", "cross-site"):
        return False
    if sec_site == "same-origin":
        return True
    if sec_site == "same-site":
        app_ref = _extract_app_ref(request)
        return bool(app_ref and app_ref.config.get("ALLOW_SAME_SITE_INTERNAL", False))
    return None


def _is_internal_request(request) -> bool:
    if _is_direct_navigation(request):
        return False

    sec_site = request.headers.get("Sec-Fetch-Site", "").lower()
    origin = request.headers.get("Origin", "")
    sec_result = _is_sec_fetch_site_internal(sec_site, origin, request)
    if sec_result is not None:
        return sec_result

    target_url = origin or request.headers.get("Referer", "")
    if target_url:
        return _is_allowed_origin(urlsplit(target_url).netloc, request)

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    return is_ajax


def internal_only(fn):
    """
    Restricts access to internal application components.
    Blocks browser direct address bar visits (Sec-Fetch-Mode: navigate) and cross-site
    browser requests. Non-browser API tools sending custom X-Requested-With or internal
    headers must be authenticated or protected via CSRF / session tokens.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(request, *args, **kwargs):
        if not _is_internal_request(request):
            return request.api_error(
                "Endpoint restricted to internal application use only.", status=403
            )

        return fn(request, *args, **kwargs)

    return wrapper


_RE_JSON_STRINGS = re.compile(r'"(?:\\.|[^"\\])*"')


def _update_depth(ch: str, depth: int) -> int:
    if ch in "{[":
        return depth + 1
    if ch in "}]" and depth > 0:
        return depth - 1
    return depth


def is_safe_json_depth(text: str, max_depth: int = 100) -> bool:
    """Check that JSON nesting depth (objects/arrays) does not exceed max_depth.

    Prevents deep recursion / stack exhaustion DoS attacks before parsing.
    """
    if not isinstance(text, str) or not text:
        return True
    depth = 0
    for ch in _RE_JSON_STRINGS.sub("", text):
        depth = _update_depth(ch, depth)
        if depth > max_depth:
            return False
    return True


def safe_json_loads(text: str, max_depth: int = 100, **kwargs: Any) -> Any:
    """Parse JSON text safely while enforcing a maximum nesting depth limit."""
    import json

    if not is_safe_json_depth(text, max_depth=max_depth):
        raise ValueError(
            f"JSON nesting depth exceeds maximum allowed limit of {max_depth}"
        )
    return json.loads(text, **kwargs)
