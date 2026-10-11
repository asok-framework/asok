from __future__ import annotations

import hashlib
import hmac
import json
import logging
import warnings
from typing import Any, Optional, Union
from urllib.parse import quote

logger = logging.getLogger("asok.security")

_UNSET = object()


def _is_valid_char(ch: str) -> bool:
    if ch in ("\r", "\n", ";", ",", " ", "\t", "\x00"):
        return False
    return 0x20 <= ord(ch) < 0x7F


def _is_safe_lang_value(lang: str) -> bool:
    """Reject anything that could break out of the Set-Cookie header value."""
    if not lang or len(lang) > 35:
        return False
    return all(_is_valid_char(ch) for ch in lang)


class SecurityMixin:
    _DEFAULT_SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        # SECURITY: Disable obsolete X-XSS-Protection header (can introduce XSS in legacy browsers; modern browsers rely on CSP)
        "X-XSS-Protection": "0",
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
        if len(key) < 32 and not self.config.get("DEBUG"):
            warnings.warn(
                "SECRET_KEY is shorter than 32 bytes. Use a strong, random key in production.",
                UserWarning,
                stacklevel=2,
            )
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature and return the original value if successful."""
        if not signed_value or "." not in signed_value:
            return None
        try:
            return self._verify_signature(signed_value)
        except (ValueError, RuntimeError) as e:
            # ValueError: rsplit failed, RuntimeError: SECRET_KEY not configured
            logger.warning(f"Signature validation error: {e}")
        except Exception as e:
            # Catch-all for unexpected errors, but log them
            logger.error(f"Unexpected error in signature validation: {e}")
        return None

    def _verify_signature(self, signed_value: str) -> Optional[str]:
        val, signature = signed_value.rsplit(".", 1)
        expected_signed = self._sign(val)
        if hmac.compare_digest(expected_signed, signed_value):
            return val
        # SECURITY: log the failed attempt for monitoring, but never the expected
        # signature or the raw value — that would leak forgeable signature material.
        logger.warning(
            "Invalid HMAC signature detected (possible tampering or expired session)."
        )
        return None

    @staticmethod
    def _session_has_user(sess: Any) -> bool:
        return bool(sess.get("user_id") or sess.get("_user_id"))

    @staticmethod
    def _client_ip_from_request(request: Any) -> str:
        return getattr(request, "client_ip", None) or request.environ.get(
            "REMOTE_ADDR", "Unknown"
        )

    def _update_session_metadata(self, request: Any) -> None:
        import time

        sess = request._session
        if not self._session_has_user(sess):
            return
        now = time.time()
        if "_created_at" not in sess:
            sess["_created_at"] = now
        sess["_ip"] = self._client_ip_from_request(request)
        sess["_user_agent"] = request.environ.get("HTTP_USER_AGENT", "Unknown")
        sess["_last_seen"] = now

    _VALID_SAMESITE = frozenset({"Strict", "Lax", "None"})

    def _resolve_samesite(self) -> str:
        raw = str(self.config.get("SESSION_SAMESITE", "Lax")).strip()
        raw = raw.replace("\r", "").replace("\n", "")
        title_val = raw.capitalize()
        return title_val if title_val in self._VALID_SAMESITE else "Lax"

    def _session_sid_cookie(self, request: Any) -> str | None:
        """Build the session SID Set-Cookie value, or None if no active session."""
        if request._session is None:
            return None
        self._update_session_metadata(request)
        sid = request._session.sid
        if request._session.modified:
            self._session_store.save(sid, request._session)
        signed = self._sign(sid)
        samesite = self._resolve_samesite()
        cookie = f"asok_sid={signed}; HttpOnly; Path=/; SameSite={samesite}; Max-Age={self.config['SESSION_TTL']}"
        if request.scheme == "https" or str(samesite).lower() == "none":
            cookie += "; Secure"
        return cookie

    def _csrf_cookie(self, request: Any) -> str | None:
        """Build the CSRF token Set-Cookie value, or None if unchanged."""
        incoming = request.cookies_dict.get(request._csrf_cookie_name)
        if not (request.csrf_token_value and request.csrf_token_value != incoming):
            return None
        cookie = f"{request._csrf_cookie_name}={request.csrf_token_value}; Path=/; SameSite=Strict; Max-Age=86400"
        if request.scheme == "https":
            cookie += "; Secure"
        return cookie

    def _flash_cookie(self, request: Any) -> str | None:
        """Build the flash-messages Set-Cookie value, or None if no change."""
        if request._new_flashes and not request._new_flashes_consumed:
            signed = self._sign(json.dumps(request._new_flashes))
            return f"{request._flash_cookie_name}={quote(signed)}; Path=/; HttpOnly; SameSite=Strict"
        if request.flashed_messages or request._new_flashes_consumed:
            return f"{request._flash_cookie_name}=; Path=/; Max-Age=0"
        return None

    def _lang_cookie(self, request: Any) -> str | None:
        """Build the language preference Set-Cookie value, or None if no lang param."""
        lang = request.args.get("lang")
        if not lang:
            return None
        if not _is_safe_lang_value(lang):
            return None
        cookie = f"asok_lang={lang}; Path=/; SameSite=Lax; HttpOnly; Max-Age=31536000"
        if request.scheme == "https":
            cookie += "; Secure"
        return cookie

    def _cookie_headers(
        self, request: Any, environ: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Determine all cookie headers (Set-Cookie) to be sent with the response."""
        headers = []
        if "asok.session_cookie" in environ:
            headers.append(("Set-Cookie", environ["asok.session_cookie"]))
        for builder in (
            self._session_sid_cookie,
            self._csrf_cookie,
            self._flash_cookie,
            self._lang_cookie,
        ):
            val = builder(request)
            if val:
                headers.append(("Set-Cookie", val))
        return headers

    def _cors_allowed(self, origin: str) -> bool:
        """Check whether the given Origin is allowed by CORS_ORIGINS config."""
        cors_origins = self.config.get("CORS_ORIGINS")
        if not cors_origins:
            return False
        if cors_origins == "*":
            return True
        # Use a cached frozenset for O(1) membership check.  Recompute when
        # CORS_ORIGINS has been changed after startup (e.g. in tests).
        if getattr(self, "_cors_origins_config_value", _UNSET) is not cors_origins:
            parsed = self._parse_cors_origins(cors_origins)
            self._cors_origins_set = frozenset(parsed)
            self._cors_origins_config_value = cors_origins
        return bool(origin) and origin in self._cors_origins_set

    def _parse_cors_origins(self, cors_origins: Any) -> list[str]:
        if isinstance(cors_origins, str):
            return self._parse_cors_str(cors_origins)
        try:
            return [str(o).strip() for o in cors_origins]
        except TypeError:
            return []

    def _parse_cors_str(self, cors_str: str) -> list[str]:
        res = []
        for o in cors_str.split(","):
            s = o.strip()
            if s:
                res.append(s)
        return res

    def _init_csp_directives(self) -> dict[str, list[str]]:
        return {
            "default-src": ["'self'"],
            "img-src": ["'self'", "data:", "blob:"],
            "style-src": ["'self'", "'unsafe-inline'"],
            "connect-src": ["'self'"],
            "object-src": ["'none'"],
            "base-uri": ["'self'"],
            "form-action": ["'self'"],
            "frame-ancestors": ["'none'"],
        }

    def _add_connect_src(
        self, request: Optional[Any], ws_port: int, directives: dict[str, list[str]]
    ) -> None:
        if not request or not hasattr(request, "host"):
            directives["connect-src"].extend(
                [
                    f"ws://127.0.0.1:{ws_port}",
                    f"ws://localhost:{ws_port}",
                    f"ws://0.0.0.0:{ws_port}",
                ]
            )
            return

        server_name = request.environ.get("SERVER_NAME", "")
        request_host = request.host.split(":")[0]

        if request_host in (server_name, "localhost", "127.0.0.1") or not server_name:
            host = request_host
            directives["connect-src"].extend(
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
            directives["connect-src"].extend(
                [
                    f"ws://{server_name}:{ws_port}",
                    f"wss://{server_name}",
                ]
            )

    def _add_script_src(
        self, nonce: Optional[str], directives: dict[str, list[str]]
    ) -> None:
        script_src = ["'self'"]
        if nonce:
            script_src.extend([f"'nonce-{nonce}'", "'strict-dynamic'"])
        directives["script-src"] = script_src

    def _is_strict_styles(self) -> bool:
        return bool(
            self.config.get("CSP_STRICT_STYLES") or self.config.get("CSP_NONCE_STYLES")
        )

    def _add_style_src(
        self, nonce: Optional[str], directives: dict[str, list[str]]
    ) -> None:
        """Configure style-src directive.

        SECURITY: If CSP_STRICT_STYLES or CSP_NONCE_STYLES is set, remove 'unsafe-inline'
        and allow styling via nonce if present, closing inline style injection vectors.
        """
        if not self._is_strict_styles():
            return
        styles = directives.get("style-src", [])
        if "'unsafe-inline'" in styles:
            styles.remove("'unsafe-inline'")
        if nonce and f"'nonce-{nonce}'" not in styles:
            styles.append(f"'nonce-{nonce}'")

    def _merge_directive_values(self, existing: list[str], values: list[str]) -> None:
        for val in values:
            if val not in existing:
                existing.append(val)

    def _extend_user_csp(self, directives: dict[str, list[str]]) -> None:
        user_csp = self.config.get("CSP", {})
        if not isinstance(user_csp, dict):
            return

        for directive, values in user_csp.items():
            parsed_values = [values] if isinstance(values, str) else values
            if directive in directives:
                self._merge_directive_values(directives[directive], parsed_values)
            else:
                directives[directive] = parsed_values

    def _is_safe_csp_report_uri(self, uri: str) -> bool:
        if any(c in uri for c in (";", "\r", "\n", "\t", " ", "\x00")):
            return False
        return uri.startswith(("/", "http://", "https://"))

    def _append_report_uri(self, csp_parts: list[str]) -> None:
        report_uri = self.config.get("CSP_REPORT_URI")
        if isinstance(report_uri, str):
            raw = report_uri.strip()
            if self._is_safe_csp_report_uri(raw):
                csp_parts.append(f"report-uri {raw}")

    def _build_csp_string(self, directives: dict[str, list[str]]) -> str:
        csp_parts = [f"{d} {' '.join(v)}" for d, v in directives.items()]
        self._append_report_uri(csp_parts)
        return "; ".join(csp_parts) + ";"

    def _build_csp(self, request: Optional[Any], nonce: Optional[str]) -> str:
        directives = self._init_csp_directives()
        ws_port = self.config.get("WS_PORT", 8001)
        self._add_connect_src(request, ws_port, directives)
        self._add_script_src(nonce, directives)
        self._add_style_src(nonce, directives)
        self._extend_user_csp(directives)
        return self._build_csp_string(directives)

    def _apply_sec_overrides(self, base: dict[str, str], sec: Any) -> None:
        if isinstance(sec, dict):
            for k, v in sec.items():
                if v is None:
                    base.pop(k, None)
                else:
                    base[k] = v

    def _build_static_security_headers_base(self) -> list[tuple[str, str]]:
        """Pre-compute security headers that are constant across requests.

        Called once at startup (or on first use). Excludes HSTS (scheme-dependent)
        and CSP (nonce-dependent); those are added per request.
        """
        sec = self.config.get("SECURITY_HEADERS", True)
        if sec is False:
            return []
        base = dict(self._DEFAULT_SECURITY_HEADERS)
        base.pop("Strict-Transport-Security", None)  # handled per request
        if isinstance(sec, dict):
            self._override_static_security_headers(base, sec)
        return list(base.items())

    def _override_static_security_headers(
        self, base: dict[str, str], sec: dict[str, Any]
    ) -> None:
        for k, v in sec.items():
            if k in ("Content-Security-Policy", "Strict-Transport-Security"):
                continue  # per-request headers
            if v is None:
                base.pop(k, None)
            else:
                base[k] = v

    def _security_headers(
        self, request: Optional[Any] = None, nonce: Optional[str] = None
    ) -> list[tuple[str, str]]:
        """Generate common security headers (HSTS, CSP, etc.)."""
        sec = self.config.get("SECURITY_HEADERS", True)
        if sec is False:
            return []

        # Use pre-computed base; copy so per-request additions don't mutate it.
        cached_base = getattr(self, "_static_security_headers_base", None)
        if cached_base is None:
            self._static_security_headers_base = (
                self._build_static_security_headers_base()
            )
            cached_base = self._static_security_headers_base

        headers = list(cached_base)
        self._add_hsts_header(headers, request)
        headers.append(("Content-Security-Policy", self._build_csp(request, nonce)))
        return self._apply_csp_override(headers, sec)

    def _add_hsts_header(
        self, headers: list[tuple[str, str]], request: Optional[Any]
    ) -> None:
        # Only send HSTS when we positively know the request came over HTTPS.
        # Sending it on a plain-HTTP site would make browsers force HTTPS
        # (and break the site) for max-age seconds.
        if request is None or request.scheme != "https":
            return
        hsts = self._DEFAULT_SECURITY_HEADERS.get("Strict-Transport-Security", "")
        if hsts:
            headers.append(("Strict-Transport-Security", hsts))

    def _apply_csp_override(
        self, headers: list[tuple[str, str]], sec: Any
    ) -> list[tuple[str, str]]:
        if not self._has_csp_override(sec):
            return headers
        v = sec["Content-Security-Policy"]
        if v is None:
            return self._remove_csp_header(headers)
        self._replace_csp_header(headers, v)
        return headers

    def _has_csp_override(self, sec: Any) -> bool:
        return isinstance(sec, dict) and "Content-Security-Policy" in sec

    def _remove_csp_header(
        self, headers: list[tuple[str, str]]
    ) -> list[tuple[str, str]]:
        return [(k, val) for k, val in headers if k != "Content-Security-Policy"]

    def _replace_csp_header(self, headers: list[tuple[str, str]], value: str) -> None:
        for i, (k, _) in enumerate(headers):
            if k == "Content-Security-Policy":
                headers[i] = ("Content-Security-Policy", value)
                break

    def _append_vary_token(self, current: str, value: str) -> str:
        tokens = [t.strip() for t in current.split(",") if t.strip()]
        return f"{current}, {value}" if value not in tokens else current

    def _add_vary_header(self, headers: list[tuple[str, str]], value: str) -> None:
        """Add a token to the Vary response header without duplicating tokens."""
        for i, (k, v) in enumerate(headers):
            if k.lower() == "vary":
                headers[i] = (k, self._append_vary_token(v, value))
                return
        headers.append(("Vary", value))

    @staticmethod
    def _has_active_session(request: Any) -> bool:
        sess = getattr(request, "_session", None)
        return bool(sess and (len(sess) > 0 or getattr(sess, "modified", False)))

    @staticmethod
    def _has_auth_context(request: Any, environ: dict[str, Any]) -> bool:
        cookies = getattr(request, "cookies_dict", {})
        if (
            getattr(request, "user_id", None) is not None
            or getattr(request, "user", None) is not None
        ):
            return True
        return bool(
            "asok.session_cookie" in environ
            or "asok_sid" in cookies
            or "asok_session" in cookies
        )

    def _maybe_add_vary_cookie(
        self,
        request: Optional[Any],
        environ: dict[str, Any],
        headers: list[tuple[str, str]],
    ) -> None:
        """SECURITY: Add Vary: Cookie to responses when session or authentication is active,
        preventing intermediate caches / CDNs from serving personalized responses to other users.
        """
        if request is None:
            return
        if self._has_active_session(request) or self._has_auth_context(
            request, environ
        ):
            self._add_vary_header(headers, "Cookie")
