from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Optional, Union
from urllib.parse import quote

logger = logging.getLogger("asok.security")


class SecurityMixin:
    _DEFAULT_SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        # SECURITY: Restrict access to sensitive browser features
        "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    }

    def _sign(self, value: Union[str, int]) -> str:
        """Sign a value using the application's secret key."""
        key = self.config.get("SECRET_KEY", "").encode()
        if not key:
            raise RuntimeError("SECRET_KEY is not configured")
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature and return the original value if successful."""
        if not signed_value or "." not in signed_value:
            return None
        try:
            val, signature = signed_value.rsplit(".", 1)
            expected_signed = self._sign(val)
            if hmac.compare_digest(expected_signed, signed_value):
                return val
            # SECURITY: Log failed signature validation attempts for monitoring
            logger.warning(
                "Invalid HMAC signature detected (possible tampering or expired session)"
            )
        except (ValueError, RuntimeError) as e:
            # ValueError: rsplit failed, RuntimeError: SECRET_KEY not configured
            logger.warning(f"Signature validation error: {e}")
        except Exception as e:
            # Catch-all for unexpected errors, but log them
            logger.error(f"Unexpected error in signature validation: {e}")
        return None

    def _cookie_headers(
        self, request: Any, environ: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Determine all cookie headers (Set-Cookie) to be sent with the response."""
        headers = []
        if "asok.session_cookie" in environ:
            headers.append(("Set-Cookie", environ["asok.session_cookie"]))
        # Always send session cookie to ensure it persists across requests
        # This is especially important for streaming responses where the session
        # may be modified after headers are sent
        if request._session is not None:
            sid = request._session.sid
            # Save if modified (will be saved again in SmartStreamer if modified during streaming)
            if request._session.modified:
                self._session_store.save(sid, request._session)
            # Always send the cookie (not just when modified)
            signed = self._sign(sid)
            # SECURITY: SameSite=Strict provides better CSRF protection than Lax
            cookie = f"asok_sid={signed}; HttpOnly; Path=/; SameSite=Strict; Max-Age={self.config['SESSION_TTL']}"
            if request.scheme == "https":
                cookie += "; Secure"
            headers.append(("Set-Cookie", cookie))
        # Only send CSRF cookie if it's new or changed
        incoming_csrf = request.cookies_dict.get(request._csrf_cookie_name)
        if request.csrf_token_value and request.csrf_token_value != incoming_csrf:
            # SECURITY: CSRF cookie must NOT be HttpOnly (JS needs to read it for AJAX)
            # SameSite=Strict provides better CSRF protection than Lax
            csrf_cookie = f"{request._csrf_cookie_name}={request.csrf_token_value}; Path=/; SameSite=Strict; Max-Age=86400"
            if request.scheme == "https":
                csrf_cookie += "; Secure"
            headers.append(("Set-Cookie", csrf_cookie))
        if request._new_flashes and not request._new_flashes_consumed:
            # Redirect case: persist new flashes for next request (HMAC-signed)
            signed_flash = self._sign(json.dumps(request._new_flashes))
            # SECURITY: SameSite=Strict provides better CSRF protection than Lax
            headers.append(
                (
                    "Set-Cookie",
                    f"{request._flash_cookie_name}={quote(signed_flash)}; Path=/; HttpOnly; SameSite=Strict",
                )
            )
        elif request.flashed_messages or request._new_flashes_consumed:
            # Old flashes displayed, or new flashes consumed inline → clear cookie
            headers.append(
                ("Set-Cookie", f"{request._flash_cookie_name}=; Path=/; Max-Age=0")
            )
        if request.args.get("lang"):
            lang_cookie = f"asok_lang={request.args['lang']}; Path=/; SameSite=Lax; Max-Age=31536000"
            if not self.config.get("DEBUG"):
                lang_cookie += "; Secure"
            headers.append(("Set-Cookie", lang_cookie))
        return headers

    def _cors_allowed(self, origin: str) -> bool:
        """Check whether the given Origin is allowed by CORS_ORIGINS config.

        Accepts CORS_ORIGINS as "*", a comma-separated string, or an iterable.
        Performs exact-match comparison on the full origin string.
        """
        cors_origins = self.config.get("CORS_ORIGINS")
        if not cors_origins:
            return False
        if cors_origins == "*":
            return True
        if isinstance(cors_origins, str):
            allowed = [o.strip() for o in cors_origins.split(",") if o.strip()]
        else:
            try:
                allowed = [str(o).strip() for o in cors_origins]
            except TypeError:
                return False
        return bool(origin) and origin in allowed

    def _security_headers(
        self, request: Optional[Any] = None, nonce: Optional[str] = None
    ) -> list[tuple[str, str]]:
        """Generate common security headers (HSTS, CSP, etc.)."""
        sec = self.config.get("SECURITY_HEADERS", True)
        if sec is False:
            return []
        base = dict(self._DEFAULT_SECURITY_HEADERS)

        # SECURITY: Only send HSTS over HTTPS (browsers ignore it over HTTP anyway)
        if request and request.scheme != "https":
            base.pop("Strict-Transport-Security", None)

        # Build CSP with configurable directives
        ws_port = self.config.get("WS_PORT", 8001)

        # SECURITY: Asok directives are pre-compiled server-side and injected as JavaScript
        # source code in the HTML. No eval() or new Function() is used at runtime, so
        # unsafe-eval is NOT required. This provides stronger CSP protection.

        # Default CSP directives
        csp_directives = {
            "default-src": ["'self'"],
            "img-src": [
                "'self'",
                "data:",
                "blob:",
            ],  # Allow data and blob URIs for image previews
            "style-src": ["'self'", "'unsafe-inline'"],
            "connect-src": ["'self'"],
            "object-src": ["'none'"],
            "base-uri": ["'self'"],
            "form-action": ["'self'"],
            "frame-ancestors": ["'none'"],
        }

        # Add host-specific connect-src if possible
        if request and hasattr(request, "host"):
            # SECURITY: Validate Host header against SERVER_NAME to prevent Host header injection
            server_name = request.environ.get("SERVER_NAME", "")
            request_host = request.host.split(":")[0]

            # Only use request host if it matches the server name or is localhost/127.0.0.1
            if (
                request_host in (server_name, "localhost", "127.0.0.1")
                or not server_name
            ):
                host = request_host
                csp_directives["connect-src"].extend(
                    [
                        f"ws://{host}:{ws_port}",
                        f"wss://{host}",
                        f"ws://{request.host}",
                        f"wss://{request.host}",
                    ]
                )
            else:
                logger.warning(
                    f"Host header mismatch: request.host={request.host}, SERVER_NAME={server_name}"
                )
                # Use SERVER_NAME instead of untrusted Host header
                csp_directives["connect-src"].extend(
                    [
                        f"ws://{server_name}:{ws_port}",
                        f"wss://{server_name}",
                    ]
                )
        else:
            csp_directives["connect-src"].extend(
                [
                    f"ws://127.0.0.1:{ws_port}",
                    f"ws://localhost:{ws_port}",
                    f"ws://0.0.0.0:{ws_port}",
                ]
            )

        # Add script-src based on nonce
        script_src = ["'self'"]
        if nonce:
            # Use 'strict-dynamic' with nonce for CSP Level 3 browsers.
            # 'self' is kept as fallback for older browsers that don't support strict-dynamic.
            # Note: 'unsafe-inline' is ignored when nonce is present, so we don't include it.
            script_src.extend([f"'nonce-{nonce}'", "'strict-dynamic'"])
            csp_directives["script-src"] = script_src
        else:
            csp_directives["script-src"] = ["'self'"]

        # Allow users to extend or override CSP directives via config
        user_csp = self.config.get("CSP", {})
        if isinstance(user_csp, dict):
            for directive, values in user_csp.items():
                if isinstance(values, str):
                    values = [values]
                if directive in csp_directives:
                    # Extend existing directive, avoiding duplicates
                    existing = csp_directives[directive]
                    for val in values:
                        if val not in existing:
                            existing.append(val)
                else:
                    # Add new directive
                    csp_directives[directive] = (
                        values if isinstance(values, list) else [values]
                    )

        # Build CSP string from directives
        csp_parts = []
        for directive, values in csp_directives.items():
            csp_parts.append(f"{directive} {' '.join(values)}")

        # SECURITY: Add report-uri for CSP violation monitoring if configured
        # This allows developers to track and respond to policy violations
        csp_report_uri = self.config.get("CSP_REPORT_URI")
        if csp_report_uri:
            csp_parts.append(f"report-uri {csp_report_uri}")

        csp = "; ".join(csp_parts) + ";"

        base["Content-Security-Policy"] = csp

        if isinstance(sec, dict):
            for k, v in sec.items():
                if v is None:
                    base.pop(k, None)
                else:
                    base[k] = v
        return list(base.items())
