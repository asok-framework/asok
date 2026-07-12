import os
import re
import unicodedata
from typing import Optional
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


def internal_only(fn):
    """
    Blocks access to an endpoint if the request does not originate from the application itself.
    Checks for XMLHttpRequest and matches the Host with the Origin/Referer.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(request, *args, **kwargs):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        host = request.headers.get("Host", "")
        referer = request.headers.get("Referer", "")
        origin = request.headers.get("Origin", "")

        is_same_origin = False
        if host:
            # SECURITY: Parse URLs properly to extract netloc, prevent substring bypass
            # e.g., Host: example.com shouldn't match Referer: https://attacker.com/example.com
            from urllib.parse import urlparse

            if referer:
                parsed_referer = urlparse(referer)
                # Compare netloc (domain:port) exactly
                if parsed_referer.netloc == host:
                    is_same_origin = True

            if origin and not is_same_origin:
                parsed_origin = urlparse(origin)
                if parsed_origin.netloc == host:
                    is_same_origin = True

        if not (is_ajax and is_same_origin):
            return request.api_error(
                "Endpoint restricted to internal application use only.", status=403
            )

        return fn(request, *args, **kwargs)

    return wrapper
