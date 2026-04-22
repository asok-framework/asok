import re
import unicodedata

_RE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]")


def secure_filename(filename: str) -> str:
    """Sanitize a filename to prevent path traversal and other security issues.

    - Removes directory separators.
    - Converts to ASCII.
    - Keeps only alphanumeric, dots, dashes, and underscores.
    - Removes leading/trailing dots and spaces.
    """
    if not filename:
        return "unnamed_file"

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

    return filename


def is_safe_url(url: str) -> bool:
    """Check if a URL is safe for redirection (relative to current host).

    Blocks protocol-relative URLs (``//``), backslash tricks (``/\\``),
    and control characters that could confuse browser URL parsers.
    """
    if not url or not isinstance(url, str):
        return False
    # Block control characters (CR, LF, tab, null) used to bypass URL parsers
    if any(c in url for c in ("\r", "\n", "\t", "\x00")):
        return False
    # Must start with / but NOT with // or /\ (open redirect vectors)
    return (
        url.startswith("/") and not url.startswith("//") and not url.startswith("/\\")
    )


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
        if host and ((referer and host in referer) or (origin and host in origin)):
            is_same_origin = True

        if not (is_ajax and is_same_origin):
            return request.api_error(
                "Endpoint restricted to internal application use only.", status=403
            )

        return fn(request, *args, **kwargs)

    return wrapper
