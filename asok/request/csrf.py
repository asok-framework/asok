from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Optional, Union
from urllib.parse import urlparse

from asok.exceptions import SecurityError
from asok.templates import SafeString


class CsrfMixin:
    """Mixin for CSRF validation and cryptographic signing on Request."""

    def csrf_input(self: Any) -> SafeString:
        """Return a hidden input field containing the CSRF token."""
        return SafeString(
            f'<input type="hidden" name="csrf_token" value="{self.csrf_token_value}" />'
        )

    def verify_csrf(self: Any) -> None:
        """Verify the CSRF token from headers, form data, or JSON body.

        Raises SecurityError if validation fails.
        """
        if self._csrf_verified:
            return

        if self.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            self._csrf_verified = True
            return

        # 1. Strict Origin/Referer verification for HTTPS
        if self.scheme == "https":
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                raise SecurityError(
                    "Strict CSRF: Origin or Referer header required for HTTPS."
                )

            try:
                parsed = urlparse(origin)
                # Compare netloc (host:port)
                if parsed.netloc != self.host:
                    raise SecurityError(
                        f"CSRF Origin mismatch: expected {self.host}, got {parsed.netloc}",
                    )
            except Exception:
                raise SecurityError("Invalid Origin or Referer format")

        # 2. Token verification
        # Priority: 1. Header (most reliable for AJAX/SPA), 2. Form, 3. JSON
        token = self.environ.get("HTTP_X_CSRF_TOKEN")

        if not token:
            headers = self.headers
            token = (
                headers.get("X-CSRF-Token")
                or headers.get("X-Csrf-Token")
                or headers.get("X-CSRF-TOKEN")
            )

        if not token:
            token = self.form.get("csrf_token")

        if not token and self.json_body and isinstance(self.json_body, dict):
            token = self.json_body.get("csrf_token")

        if not token or not hmac.compare_digest(
            str(token), str(self.csrf_token_value or "")
        ):
            raise SecurityError("CSRF validation failed")

        self._csrf_verified = True

        # SECURITY: Rotate CSRF token after successful validation
        # to prevent token reuse attacks
        new_token = secrets.token_hex(32)
        self.csrf_token_value = new_token
        # Le nouveau token sera automatiquement envoyé via Set-Cookie et X-CSRF-Token header

    def _sign(self: Any, value: Union[str, int]) -> str:
        """Sign a value using the application's secret key."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if app_ref:
            return app_ref._sign(value)
        # Fallback (mostly for tests without full app env)
        key = self.environ.get("asok.secret_key", "").encode()
        if not key:
            raise RuntimeError("SECRET_KEY is not configured")
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self: Any, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature of a value and return the original if valid."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if app_ref:
            return app_ref._unsign(signed_value)
        # Fallback
        if not signed_value or "." not in signed_value:
            return None
        try:
            val, sig = signed_value.rsplit(".", 1)
            if hmac.compare_digest(self._sign(val), signed_value):
                return val
        except Exception:
            pass
        return None
