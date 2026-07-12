from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from asok.exceptions import RedirectException
from asok.utils.security import is_safe_url, request_authority, secure_filename


class ResponseMixin:
    """Mixin for response manipulation, redirects and file downloads on Request."""

    def header(self: Any, name: str, value: str) -> Any:
        """Add a custom response header.

        Args:
            name: Header name.
            value: Header value.

        Returns:
            The Request instance for chaining.
        """
        # SECURITY: sanitize header name and value to prevent HTTP response splitting
        clean_name = str(name).replace("\r", "").replace("\n", "")
        clean_value = str(value).replace("\r", "").replace("\n", "")
        self.response_headers.append((clean_name, clean_value))
        return self

    def json(self: Any, data: Any) -> str:
        """Return a JSON response."""
        self.content_type = "application/json"
        return json.dumps(data)

    def api(self: Any, data: Any, status: int = 200) -> str:
        """Return a standardized JSON API success response."""
        self.status_code(status)
        self.content_type = "application/json"
        return json.dumps({"data": data, "status": status})

    def api_error(
        self: Any,
        message: str,
        status: int = 400,
        errors: Optional[dict[str, Any]] = None,
    ) -> str:
        """Return a standardized JSON API error response."""
        self.status_code(status)
        self.content_type = "application/json"
        payload: dict[str, Any] = {"error": message, "status": status}
        if errors is not None:
            payload["errors"] = errors
        return json.dumps(payload)

    def redirect(self: Any, url: str, safe: bool = True) -> None:
        """Abort current request and redirect to the given URL.

        If safe=True (default), redirects to external domains are blocked.
        """
        if safe and not is_safe_url(url, allowed_host=request_authority(self)):
            raise ValueError(
                f"Potentially unsafe redirect blocked: {url}. "
                "Use safe=False for external redirects."
            )

        raise RedirectException(url)

    def back(self: Any, default: str = "/") -> None:
        """Abort current request and redirect back to the previous page (Referer).
        If no Referer is present, it redirects to the provided 'default' URL.

        SECURITY: Validates the referrer URL to prevent open redirect attacks.
        Only allows same-origin URLs or relative paths.
        """
        ref = self.environ.get("HTTP_REFERER", default)

        # SECURITY: Validate redirect URL to prevent open redirect attacks
        if not self._is_safe_redirect(ref):
            ref = default

        self.redirect(ref)

    def _is_same_origin(self: Any, parsed: Any) -> bool:
        # Get current request's origin
        current_scheme = self.environ.get("wsgi.url_scheme", "http")
        current_host = request_authority(self)
        return parsed.scheme == current_scheme and parsed.netloc == current_host

    def _parse_and_check_url(self: Any, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if not parsed.scheme and not parsed.netloc:
                return True
            return self._is_same_origin(parsed)
        except Exception:
            return False

    def _is_safe_redirect(self: Any, url: str) -> bool:
        """Validate that a redirect URL is safe (same-origin or relative path).

        SECURITY: Prevents open redirect attacks by ensuring redirects only go to:
        - Relative paths (e.g., "/dashboard", "users/profile")
        - Same-origin URLs (same scheme, host, and port as current request)

        Returns:
            True if the URL is safe to redirect to, False otherwise.
        """
        if not url:
            return False

        # Allow relative paths (no scheme/host)
        if url.startswith("/") and not url.startswith("//"):
            return True

        return self._parse_and_check_url(url)

    def _extract_filepath_parts(self: Any, filepath: str) -> list[str]:
        from pathlib import Path as _Path

        p = _Path(filepath)
        parts = []
        for part in p.parts:
            if part not in ("/", "\\", ""):
                parts.append(part)
        return parts

    def _resolve_and_check_relative(
        self: Any, base_dir: "Path", parts: list[str]
    ) -> tuple["Path | None", str]:
        from pathlib import Path as _Path

        try:
            full_path = (base_dir / _Path(*parts)).resolve(strict=False)
        except (ValueError, OSError, RuntimeError):
            return None, "<h1>404 Not Found</h1>"
        try:
            full_path.relative_to(base_dir)
            return full_path, ""
        except ValueError:
            return None, "<h1>403 Forbidden</h1>"

    def _validate_send_path(
        self: Any, filepath: str, base_dir: "Path"
    ) -> tuple["Path | None", str]:
        """Resolve and validate a send_file path. Returns (full_path, error_html) or (None, error_html)."""
        parts = self._extract_filepath_parts(filepath)
        if not parts:
            return None, "<h1>403 Forbidden</h1>"
        return self._resolve_and_check_relative(base_dir, parts)

    def _check_symlinks(self: Any, full_path: "Path", base_dir: "Path") -> bool:
        """Return True if any path component is a symlink (disallowed)."""
        current = full_path
        while current != base_dir:
            if current.is_symlink():
                return True
            current = current.parent
            if current == current.parent:
                break
        return False

    def _check_file_validity(
        self: Any, full_path: "Path", base_dir: "Path"
    ) -> Optional[str]:
        if self._check_symlinks(full_path, base_dir):
            self.status = "403 Forbidden"
            return "<h1>403 Forbidden: Symlinks not allowed</h1>"

        if not full_path.is_file():
            self.status = "404 Not Found"
            return "<h1>404 Not Found</h1>"

        if full_path.stat().st_size > 100 * 1024 * 1024:
            self.status = "413 Payload Too Large"
            return "<h1>413 Payload Too Large</h1>"
        return None

    def _set_download_headers(
        self: Any,
        full_path: "Path",
        filename: Optional[str],
        as_attachment: bool,
        mimetype: Optional[str],
    ) -> None:
        fname = secure_filename(filename or full_path.name)
        disposition = "attachment" if as_attachment else "inline"
        file_size = full_path.stat().st_size

        self.content_type = mimetype or "application/octet-stream"
        self.environ.setdefault("asok.extra_headers", []).extend(
            [
                ("Content-Disposition", f'{disposition}; filename="{fname}"'),
                ("Content-Length", str(file_size)),
            ]
        )

        if file_size > 5 * 1024 * 1024:
            self.environ["asok.stream_file"] = str(full_path)
        else:
            with open(full_path, "rb") as f:
                self.environ["asok.binary_response"] = f.read()

    def send_file(
        self: Any,
        filepath: str,
        filename: Optional[str] = None,
        as_attachment: bool = True,
        base_dir: Optional[str] = None,
    ) -> str:
        """Return a file download response.

        Args:
            filepath:      Path to the file (relative to base_dir).
            filename:      Download filename (defaults to basename).
            as_attachment: If True, browser downloads; if False, inline display.
            base_dir:      Absolute directory to resolve filepath against.
                           Defaults to <project_root>/src/partials/uploads.

        SECURITY: Enhanced path traversal protection using pathlib.
        """
        root = Path(self.environ.get("asok.root", os.getcwd()))
        resolved_base = (
            Path(base_dir).resolve()
            if base_dir is not None
            else (root / "src/partials/uploads").resolve()
        )
        base_dir = resolved_base

        full_path, error = self._validate_send_path(filepath, base_dir)
        if full_path is None:
            self.status = "403 Forbidden" if "403" in error else "404 Not Found"
            return error

        file_error = self._check_file_validity(full_path, base_dir)
        if file_error:
            return file_error

        mimetype, _ = mimetypes.guess_type(str(full_path))
        self._set_download_headers(full_path, filename, as_attachment, mimetype)
        return ""
