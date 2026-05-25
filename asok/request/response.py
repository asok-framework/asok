from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from asok.exceptions import RedirectException
from asok.utils.security import is_safe_url, secure_filename


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
        self.response_headers.append((name, value))
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
        if safe and not is_safe_url(
            url, allowed_host=self.environ.get("HTTP_HOST") or self.host
        ):
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

        # Parse the URL to check if it's same-origin
        try:
            parsed = urlparse(url)

            # If no scheme, it's a relative URL - allow it
            if not parsed.scheme and not parsed.netloc:
                return True

            # Get current request's origin
            current_scheme = self.environ.get("wsgi.url_scheme", "http")
            current_host = self.environ.get("HTTP_HOST") or self.environ.get(
                "SERVER_NAME", ""
            )

            # Only allow same-origin redirects
            return parsed.scheme == current_scheme and parsed.netloc == current_host

        except Exception:
            # If parsing fails, reject the URL to be safe
            return False

    def send_file(
        self: Any,
        filepath: str,
        filename: Optional[str] = None,
        as_attachment: bool = True,
    ) -> str:
        """Return a file download response.

        Args:
            filepath:      Path to the file (absolute or relative to project root).
            filename:      Download filename (defaults to basename).
            as_attachment:  If True, browser downloads; if False, inline display.

        SECURITY: Enhanced path traversal protection using pathlib.
        """
        root = Path(self.environ.get("asok.root", os.getcwd()))
        base_dir = (root / "src/partials/uploads").resolve()

        # SECURITY: Strip root/drive prefix to prevent absolute path override
        p = Path(filepath)
        parts = [part for part in p.parts if part not in ("/", "\\", "")]
        if not parts:
            self.status = "403 Forbidden"
            return "<h1>403 Forbidden</h1>"
        safe_path = Path(*parts)

        # Resolve the full path
        try:
            full_path = (base_dir / safe_path).resolve(strict=False)
        except (ValueError, OSError, RuntimeError):
            # File doesn't exist or path resolution failed
            self.status = "404 Not Found"
            return "<h1>404 Not Found</h1>"

        # SECURITY: Verify the resolved path is still within the base directory
        try:
            full_path.relative_to(base_dir)
        except ValueError:
            # Path escapes the base directory
            self.status = "403 Forbidden"
            return "<h1>403 Forbidden</h1>"

        # SECURITY: Check for symlinks in the entire path chain
        # This prevents attacks using symlinks in parent directories
        current = full_path
        while current != base_dir:
            if current.is_symlink():
                self.status = "403 Forbidden"
                return "<h1>403 Forbidden: Symlinks not allowed</h1>"
            current = current.parent
            if current == current.parent:  # Reached root
                break

        # Verify it's a regular file
        if not full_path.is_file():
            self.status = "404 Not Found"
            return "<h1>404 Not Found</h1>"

        mimetype, _ = mimetypes.guess_type(str(full_path))

        # Determine filename and sanitize it for the header
        fname = secure_filename(filename or full_path.name)

        disposition = "attachment" if as_attachment else "inline"
        file_size = full_path.stat().st_size

        # SECURITY: Prevent DoS by limiting max file size (100 MB)
        MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
        if file_size > MAX_FILE_SIZE:
            self.status = "413 Payload Too Large"
            return "<h1>413 Payload Too Large</h1>"

        self.content_type = mimetype or "application/octet-stream"
        self.environ.setdefault("asok.extra_headers", []).extend(
            [
                ("Content-Disposition", f'{disposition}; filename="{fname}"'),
                ("Content-Length", str(file_size)),
            ]
        )

        # Stream large files (> 5 MB) to avoid loading them entirely into memory
        if file_size > 5 * 1024 * 1024:
            self.environ["asok.stream_file"] = str(full_path)
        else:
            with open(full_path, "rb") as f:
                self.environ["asok.binary_response"] = f.read()

        return ""
