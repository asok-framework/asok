from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional, Union
from urllib.parse import urlparse

from asok.exceptions import SecurityError
from asok.templates import SafeString


class CsrfMixin:
    """Mixin for CSRF validation and cryptographic signing on Request."""

    def csrf_input(self: Any) -> SafeString:
        """Return a hidden input field containing the CSRF token."""
        import html

        escaped_token = html.escape(self.csrf_token_value or "")
        return SafeString(
            f'<input type="hidden" name="csrf_token" value="{escaped_token}" />'
        )

    def _verify_csrf_origin(self: Any) -> None:
        """Verify Origin/Referer header for HTTPS requests. Raises SecurityError on failure."""
        origin = self.headers.get("Origin") or self.headers.get("Referer")
        if not origin:
            raise SecurityError(
                "Strict CSRF: Origin or Referer header required for HTTPS."
            )
        parsed_origin, parsed_req = self._parse_csrf_origins(origin)

        if parsed_origin.scheme != parsed_req.scheme:
            raise SecurityError(
                f"CSRF Origin mismatch: expected scheme {parsed_req.scheme}, got {parsed_origin.scheme}"
            )
        if parsed_origin.hostname != parsed_req.hostname:
            raise SecurityError(
                f"CSRF Origin mismatch: expected host {parsed_req.hostname}, got {parsed_origin.hostname}"
            )
        self._verify_csrf_port_if_strict(parsed_origin, parsed_req)

    def _verify_csrf_port_if_strict(
        self: Any, parsed_origin: Any, parsed_req: Any
    ) -> None:
        app_ref = self.environ.get("asok.app")
        if not (app_ref and app_ref.config.get("STRICT_CSRF_PORT")):
            return
        origin_port = self._effective_port_val(parsed_origin)
        req_port = self._effective_port_val(parsed_req)
        if origin_port != req_port:
            raise SecurityError(
                f"CSRF Port mismatch: expected {req_port}, got {origin_port}"
            )

    def _effective_port_val(self: Any, parsed: Any) -> int:
        return parsed.port or (443 if parsed.scheme == "https" else 80)

    def _parse_csrf_origins(self: Any, origin: str) -> tuple[Any, Any]:
        try:
            parsed_origin = urlparse(origin)
            request_host = self.environ.get("HTTP_HOST", "localhost")
            parsed_req = urlparse(f"{self.scheme}://{request_host}")
            return parsed_origin, parsed_req
        except Exception:
            raise SecurityError("Invalid Origin or Referer format")

    def _get_csrf_header_token(self: Any) -> Optional[str]:
        token = self.environ.get("HTTP_X_CSRF_TOKEN")
        if token:
            return token
        headers = self.headers
        for key in ("X-CSRF-Token", "X-Csrf-Token", "X-CSRF-TOKEN"):
            token = headers.get(key)
            if token:
                return token
        return None

    def _get_csrf_json_token(self: Any) -> Optional[str]:
        if self.json_body and isinstance(self.json_body, dict):
            return self.json_body.get("csrf_token")
        return None

    def _extract_csrf_token(self: Any) -> Optional[str]:
        """Extract CSRF token from header, form, or JSON body."""
        token = self._get_csrf_header_token()
        if token:
            return token

        form_token = self.form.get("csrf_token")
        if form_token and "csrf_token" not in getattr(self, "args", {}):
            return form_token

        return self._get_csrf_json_token()

    def _verify_token_match(self: Any, token: Optional[str]) -> None:
        expected = str(self.csrf_token_value or "")
        if not token or not hmac.compare_digest(str(token), expected):
            raise SecurityError("CSRF validation failed")

    def verify_csrf(self: Any) -> None:
        """Verify the CSRF token from headers, form data, or JSON body.

        Raises SecurityError if validation fails.

        NOTE: The token is intentionally NOT rotated after each validation.
        Per-request rotation desynchronises the cookie (updated) from the
        rendered HTML form (still holds the old token), causing a 403 on
        every second submission.  Token rotation is already performed on
        login and logout (see auth.py) which is the appropriate boundary.
        """
        if self._csrf_verified:
            return

        if self.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            self._csrf_verified = True
            return

        # 1. Strict Origin/Referer verification for HTTPS
        if self.scheme == "https":
            self._verify_csrf_origin()

        # 2. Token verification
        token = self._extract_csrf_token()
        self._verify_token_match(token)

        self._csrf_verified = True

    def _sign(self: Any, value: Union[str, int]) -> str:
        """Sign a value using the application's secret key."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if app_ref:
            return app_ref._sign(value)
        # Fallback for tests that build Request without a full app context
        import os as _os

        key = _os.environ.get("SECRET_KEY", "").encode()
        if not key:
            raise RuntimeError("SECRET_KEY is not configured")
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign_fallback(self: Any, signed_value: Optional[str]) -> Optional[str]:
        if not signed_value or "." not in signed_value:
            return None
        try:
            val, sig = signed_value.rsplit(".", 1)
            if hmac.compare_digest(self._sign(val), signed_value):
                return val
        except Exception:
            pass
        return None

    def _unsign(self: Any, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature of a value and return the original if valid."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if app_ref:
            return app_ref._unsign(signed_value)
        return self._unsign_fallback(signed_value)
