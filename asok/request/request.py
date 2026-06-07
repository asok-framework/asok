from __future__ import annotations

import json
import re
import secrets
from typing import Any, Optional, Union
from urllib.parse import parse_qsl, unquote

from asok.exceptions import (
    AbortException,
    ForbiddenError,
    NotFoundError,
    RedirectException,
    UnauthorizedError,
)
from asok.utils.geo import IPLocation
from asok.utils.security import secure_filename

from .auth import AuthMixin
from .csrf import CsrfMixin
from .env import _Env
from .metadata import Metadata
from .query_dict import QueryDict
from .response import ResponseMixin
from .session import SessionMixin
from .template import TemplateMixin
from .upload import UploadedFile
from .user_agent import UserAgent

# Pre-compiled regex for multipart parsing
_RE_NAME = re.compile(r'name="([^"]+)"')
_RE_FILENAME = re.compile(r'filename="([^"]+)"')


class Request(
    AuthMixin,
    SessionMixin,
    CsrfMixin,
    TemplateMixin,
    ResponseMixin,
):
    """The central Request object, handling everything from input parsing to response rendering."""

    # Status code mapping
    _STATUS_MAP = {
        200: "200 OK",
        201: "201 Created",
        204: "204 No Content",
        301: "301 Moved Permanently",
        302: "302 Found",
        304: "304 Not Modified",
        400: "400 Bad Request",
        401: "401 Unauthorized",
        403: "403 Forbidden",
        404: "404 Not Found",
        405: "405 Method Not Allowed",
        413: "413 Payload Too Large",
        500: "500 Internal Server Error",
    }

    def __init__(self, environ: dict[str, Any]):
        self.environ: dict[str, Any] = environ
        self.path: str = environ.get("PATH_INFO", "/")
        self.method: str = environ.get("REQUEST_METHOD", "GET")
        self.query_string: str = unquote(environ.get("QUERY_STRING", ""))

        self.body: bytes = b""
        self._csrf_verified = False
        self.args: QueryDict = QueryDict(parse_qsl(self.query_string))
        self.form: dict[str, str] = dict(self.args)
        self.files: dict[str, UploadedFile] = {}
        self.all_files: list[UploadedFile] = []
        self._files_multi: dict[str, list[tuple[str, UploadedFile]]] = {}
        self.json_body: Optional[Any] = None
        self.content_type: str = "text/html; charset=utf-8"
        self.status: str = "200 OK"
        self.params: dict[str, str] = {}
        self.response_headers: list[tuple[str, str]] = []

        # SECURITY: Use proper RFC 6265 cookie parsing with URL decoding
        self.cookies_dict = {}
        cookie_header = environ.get("HTTP_COOKIE", "")
        if cookie_header:
            from http.cookies import SimpleCookie

            try:
                cookies = SimpleCookie()
                cookies.load(cookie_header)
                for key, morsel in cookies.items():
                    # URL-decode cookie values (they may be encoded)
                    self.cookies_dict[key] = unquote(morsel.value)
            except Exception:
                # Fallback to manual parsing if SimpleCookie fails
                import logging

                logger = logging.getLogger("asok.request")
                logger.warning(
                    "Failed to parse cookies with SimpleCookie, using fallback"
                )
                for pair in cookie_header.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        val = v.strip()
                        # Handle optional quotes
                        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                            val = val[1:-1]
                        self.cookies_dict[k.strip()] = unquote(val)

        self._csrf_cookie_name = "asok_csrf"
        self.csrf_token_value = self.cookies_dict.get(self._csrf_cookie_name)
        if not self.csrf_token_value:
            # Generate new CSRF token
            self.csrf_token_value = secrets.token_hex(32)
            # SECURITY: Mark CSRF token to be set as cookie in response
            # This ensures first-time users get the token
            if "asok.csrf_token_new" not in environ:
                environ["asok.csrf_token_new"] = self.csrf_token_value

        self._user_instance = None
        self._auth_resolved = False
        self._session = None
        self.env = _Env()

        self.lang = self.form.get("lang", self.cookies_dict.get("asok_lang"))
        if not self.lang:
            accept_lang = environ.get("HTTP_ACCEPT_LANGUAGE", "")
            if accept_lang:
                self.lang = accept_lang.split(",")[0].split("-")[0].split(";")[0]

        app_ref = environ.get("asok.app")
        self.lang = self.lang or (app_ref.config.get("LOCALE") if app_ref else "en")

        self._flash_cookie_name = "asok_flash"
        self.flashed_messages: list[dict[str, str]] = []
        self._new_flashes: list[dict[str, str]] = []
        self._new_flashes_consumed: bool = False
        self.meta: Metadata = Metadata()
        self.page_id: Optional[str] = None
        self.scoped_assets: dict[str, Optional[str]] = {"css": None, "js": None}
        self._nonce: Optional[str] = None
        raw_flash = self.cookies_dict.get(self._flash_cookie_name)
        if raw_flash:
            try:
                # Cookie payload is HMAC-signed to prevent spoofing. Legacy
                # unsigned cookies (pre-upgrade) are silently ignored.
                unsigned = self._unsign(unquote(raw_flash))
                if unsigned:
                    self.flashed_messages = json.loads(unsigned)
            except Exception as e:
                # SECURITY: Log failed flash message deserialization (could indicate tampering)
                import logging

                logger = logging.getLogger("asok.security")
                logger.warning("Failed to deserialize flash messages: %s", e)

        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            content_length = 0

        # SECURITY: Initialize body rejection flag
        self._body_rejected = False

        app_ref = environ.get("asok.app")
        max_body = (
            app_ref.config.get("MAX_CONTENT_LENGTH", 10 * 1024 * 1024)
            if app_ref
            else 10 * 1024 * 1024
        )
        if content_length > max_body:
            self.status = "413 Payload Too Large"
            self._body_rejected = True
            content_length = 0

        # SECURITY: Only read body if size is valid AND not rejected
        if content_length > 0 and not self._body_rejected:
            self.body = environ["wsgi.input"].read(content_length)
            enc_content_type = environ.get("CONTENT_TYPE", "")

            if "application/x-www-form-urlencoded" in enc_content_type:
                self.form.update(dict(parse_qsl(self.body.decode("utf-8"))))
            elif "application/json" in enc_content_type:
                try:
                    # SECURITY: MAX_CONTENT_LENGTH prevents large payloads
                    # RecursionError prevents deeply nested JSON DoS attacks
                    self.json_body = json.loads(self.body.decode("utf-8"))
                except (json.JSONDecodeError, RecursionError) as e:
                    # Log deeply nested JSON attempts for security monitoring
                    if isinstance(e, RecursionError):
                        import logging

                        logger = logging.getLogger("asok.security")
                        logger.warning(
                            "Rejected deeply nested JSON payload (possible DoS attempt)"
                        )
                    pass
            elif "multipart/form-data" in enc_content_type:
                # SECURITY: Extract boundary with proper quote handling (RFC 2046)
                # Boundary may be quoted: boundary="----WebKitFormBoundary"
                boundary_match = re.search(
                    r'boundary=([^;\s]+|"[^"]+")', enc_content_type
                )
                if boundary_match:
                    boundary_raw = boundary_match.group(1).strip()
                    # Remove quotes if present
                    if boundary_raw.startswith('"') and boundary_raw.endswith('"'):
                        boundary_raw = boundary_raw[1:-1]
                    boundary = boundary_raw.encode()
                    # Split body by boundary
                    parts = self.body.split(b"--" + boundary)
                    for part in parts:
                        if not part or part == b"--\r\n" or part == b"--":
                            continue

                        # Split headers and content
                        if b"\r\n\r\n" not in part:
                            continue

                        head, payload = part.split(b"\r\n\r\n", 1)
                        # Remove trailing \r\n from payload
                        if payload.endswith(b"\r\n"):
                            payload = payload[:-2]

                        head_str = head.decode("utf-8", errors="ignore")
                        cdisp = ""
                        for line in head_str.split("\r\n"):
                            if line.lower().startswith("content-disposition:"):
                                cdisp = line
                                break

                        if cdisp and "name=" in cdisp:
                            name_match = _RE_NAME.search(cdisp)
                            filename_match = _RE_FILENAME.search(cdisp)
                            if name_match:
                                name = name_match.group(1)
                                if filename_match:
                                    raw_filename = filename_match.group(1)
                                    if (
                                        raw_filename
                                    ):  # Only if a file was actually selected
                                        safe_filename = secure_filename(raw_filename)
                                        uploaded = UploadedFile(safe_filename, payload)
                                        self.files[name] = uploaded
                                        self.all_files.append(uploaded)
                                        self._files_multi.setdefault(name, []).append(
                                            (name, uploaded)
                                        )
                                else:
                                    # Regular form field
                                    self.form[name] = payload.decode(
                                        "utf-8", errors="ignore"
                                    )

    def file(self, name: str) -> Optional[UploadedFile]:
        """Get a single uploaded file by form field name."""
        return self.files.get(name)

    def file_list(self, name: Optional[str] = None) -> list[UploadedFile]:
        """Get uploaded files, optionally filtered by form field name.

        Args:
            name: Form field name to filter by, or None for all files.
        """
        if name is None:
            return list(self.all_files)
        return [f for _, f in self._files_multi.get(name, [])]

    def flash(self, category: str, message: str) -> None:
        """Store a temporary message to be displayed on the next page rendering."""
        self._new_flashes.append({"category": category, "message": message})

    def get_flashed_messages(self) -> list[dict[str, str]]:
        """Retrieve all flash messages (current and newly added)."""
        messages = self.flashed_messages + self._new_flashes
        if self._new_flashes:
            self._new_flashes_consumed = True
        return messages

    def rate_limit(
        self,
        limit: Union[str, int],
        window: Optional[int] = None,
        prefix: Optional[str] = None,
    ) -> None:
        """Programmatically check a rate limit against the current request.

        Raises RateLimitExceeded if the limit is exceeded.
        """
        from asok.ratelimit import RateLimit

        # Auto-generate a prefix based on the caller's stack frame if not provided
        if not prefix:
            import inspect
            frame = inspect.currentframe()
            try:
                # Walk up to the caller's frame (the view function calling request.rate_limit)
                caller = frame.f_back
                if caller:
                    func_name = caller.f_code.co_name
                    module_name = caller.f_globals.get("__name__", "view")
                    prefix = f"rl:{module_name}.{func_name}"
            finally:
                del frame
            if not prefix:
                prefix = f"rl:{self.path}"

        limiter = RateLimit(limit, window, prefix=prefix)
        limiter.check(self)

    def abort(self, code: int, message: Optional[str] = None) -> None:
        """Raise an AbortException with the given status code and message."""
        raise AbortException(code, message)

    def abort_404(
        self, message: Optional[str] = "The requested resource was not found"
    ) -> None:
        """Shortcut for raising NotFoundError (404)."""
        raise NotFoundError(message)

    def abort_403(
        self,
        message: Optional[str] = "You do not have permission to access this resource",
    ) -> None:
        """Shortcut for raising ForbiddenError (403)."""
        raise ForbiddenError(message)

    def abort_401(
        self,
        message: Optional[str] = "Authentication is required to access this resource",
    ) -> None:
        """Shortcut for raising UnauthorizedError (401)."""
        raise UnauthorizedError(message)

    def login_required(self) -> None:
        """Enforce authentication, aborting with 401 if not logged in."""
        if not self.user:
            self.abort_401()

    def not_found(self, message: Optional[str] = None) -> None:
        """Shortcut for abort(404)."""
        self.abort_404(message)

    def forbidden(self, message: Optional[str] = None) -> None:
        """Shortcut for abort(403)."""
        self.abort(403, message)

    def unauthorized(self, message: Optional[str] = None) -> None:
        """Shortcut for abort(401)."""
        self.abort(401, message)

    def method_not_allowed(
        self, allowed: list[str], message: Optional[str] = None
    ) -> None:
        """Abort current request with 405 Method Not Allowed and set the 'Allow' header."""
        self.header("Allow", ", ".join(allowed))
        self.abort(
            405,
            message
            or f"Method {self.method} not allowed. Supported methods: {', '.join(allowed)}",
        )

    @property
    def code(self) -> int:
        """The HTTP status code as an integer (e.g., 200, 404)."""
        try:
            return int(self.status.split(" ")[0])
        except (ValueError, IndexError, AttributeError):
            return 200

    def status_code(self, code: Optional[int] = None) -> Union[Request, int]:
        """Set or get the HTTP status code for the response.

        If a code is provided, sets it and returns the Request instance (chainable).
        If no code is provided, returns the current integer status code.
        """
        if code is None:
            return self.code
        self.status = self._STATUS_MAP.get(code, f"{code} Unknown")
        return self

    @property
    def nonce(self) -> str:
        """Return a stable security nonce for the current request.

        The nonce is generated on first access and cached for the duration of the request.
        """
        # Generate nonce only once per request (idempotent)
        if self._nonce is None:
            self._nonce = secrets.token_urlsafe(16)
        return self._nonce

    def _session_cookie(self, value: str, max_age: Optional[int] = None) -> str:
        """Formats the session cookie header."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        samesite = "Lax"
        if app_ref:
            samesite = app_ref.config.get("SESSION_SAMESITE", "Lax")

        cookie = f"asok_session={value}; HttpOnly; Path=/; SameSite={samesite}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        if app_ref and not app_ref.config.get("DEBUG"):
            cookie += "; Secure"
        return cookie

    def __(self, key: str, **kwargs: Any) -> str:
        """Translate a key using the current request language."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "locales"):
            return key
        lang_dict = app_ref.locales.get(self.lang, app_ref.locales.get("en", {}))
        text = lang_dict.get(key, key)
        if kwargs:
            try:
                return text.format(**kwargs)
            except Exception:
                pass
        return text

    @property
    def ip(self) -> str:
        """Get the client IP address (respects TRUSTED_PROXIES config).

        X-Forwarded-For is only trusted when the direct connection comes from
        a configured trusted proxy. Set TRUSTED_PROXIES to "*" or a list of IPs.
        """
        remote_addr = self.environ.get("REMOTE_ADDR", "")
        forwarded = self.environ.get("HTTP_X_FORWARDED_FOR")
        if not forwarded:
            return remote_addr

        app_ref = self.environ.get("asok.app")
        trusted = app_ref.config.get("TRUSTED_PROXIES") if app_ref else None
        if trusted is None:
            return remote_addr
        if trusted != "*" and remote_addr not in trusted:
            return remote_addr
        return forwarded.split(",")[0].strip()

    @property
    def user_agent(self) -> str:
        """Get the raw User-Agent string."""
        return self.environ.get("HTTP_USER_AGENT", "")

    @property
    def browser(self) -> UserAgent:
        """Get a parsed UserAgent helper object."""
        cache = self.__dict__.setdefault("_browser_cache", None)
        if cache:
            return cache
        self.__dict__["_browser_cache"] = UserAgent(self.user_agent)
        return self.__dict__["_browser_cache"]

    @property
    def geo(self) -> dict[str, Any]:
        """Get comprehensive geographic information for the current request.

        Combines IP-based location (city, lat, lon) with rich country metadata
        (flag, currency, timezone, etc.).
        """
        cache = self.__dict__.setdefault("_geo_cache", None)
        if cache:
            return cache

        # 1. Basic IP location (city, country code, lat, lon)
        loc = IPLocation.get_instance().lookup(self.ip)

        # 2. Enrich with framework country data
        from asok.utils.geo import Countries

        country_code = loc.get("country", "")
        if not country_code or country_code == "Unknown":
            # Fallback based on request language
            lang_to_country = {
                "fr": "FR",
                "en": "US",
                "es": "ES",
                "de": "DE",
                "it": "IT",
                "ja": "JP",
                "zh": "CN",
                "pt": "BR",
                "ru": "RU",
            }
            country_code = lang_to_country.get(self.lang, "US")

        country_info = Countries.get(country_code) or {
            "iso": "Unknown",
            "name": "Unknown",
            "dial_code": "",
            "flag": "🌐",
            "capital": "Unknown",
            "continent": "Unknown",
            "currency": "Unknown",
            "languages": "Unknown",
        }

        city = loc.get("city", "Unknown")
        if (not city or city == "Unknown") and country_info.get("capital") != "Unknown":
            city = country_info.get("capital", "Unknown")

        geo_data = {
            "ip": self.ip,
            "city": city,
            "country": country_code,
            "lat": loc.get("lat", 0.0),
            "lon": loc.get("lon", 0.0),
            **country_info,
        }

        # 3. Add derived info
        from asok.utils.geo import Countries as GeoUtils

        geo_data["timezone"] = GeoUtils.get_timezone(geo_data["iso"])

        self.__dict__["_geo_cache"] = geo_data
        return geo_data

    @property
    def location(self) -> dict[str, Any]:
        """Legacy alias for request.geo (for backward compatibility)."""
        return self.geo

    def require_auth(self, redirect_url: str = "/login") -> None:
        """Ensure the user is authenticated, else redirect to login with a 'next' parameter."""
        if not self.is_authenticated:
            from urllib.parse import quote

            next_url = self.path
            if self.query_string:
                next_url += "?" + self.query_string

            # SECURITY: Validate next_url to prevent open redirect
            # Only allow relative URLs (no protocol, no different host)
            if next_url and ("://" in next_url or next_url.startswith("//")):
                # Absolute URL - potential open redirect attack
                import logging

                logger = logging.getLogger("asok.security")
                logger.warning("Open redirect attempt blocked: %s", next_url)
                next_url = "/"

            # URL-encode the next parameter to prevent injection
            next_encoded = quote(next_url, safe="/?&=")
            separator = "&" if "?" in redirect_url else "?"
            raise RedirectException(f"{redirect_url}{separator}next={next_encoded}")

    @property
    def scheme(self) -> str:
        """The URL scheme (http or https)."""
        return self.environ.get("wsgi.url_scheme", "http")

    @property
    def host(self) -> str:
        """The Host header value with validation against injection attacks."""
        host = self.environ.get("HTTP_HOST", "localhost")

        # SECURITY: Validate Host header format to prevent injection
        # Block control characters that could be used in attacks
        if any(c in host for c in ("\r", "\n", "\t", " ", "\x00")):
            import logging

            logger = logging.getLogger("asok.security")
            logger.warning("Malformed Host header detected: %r", host)
            return "localhost"

        # Remove port number if present (for comparison purposes)
        # Handle IPv6: [::1]:8080 format
        if host.startswith("["):
            # IPv6 with port: [::1]:8080
            if "]:" in host:
                return host.split("]:")[0] + "]"
            return host
        elif ":" in host:
            # IPv4 or hostname with port
            return host.split(":")[0]

        return host

    @property
    def headers(self) -> dict[str, str]:
        """A dictionary of all request headers."""
        if hasattr(self, "_headers_cache"):
            return self._headers_cache

        headers = {}
        for key, value in self.environ.items():
            if key.startswith("HTTP_"):
                # HTTP_X_CSRF_TOKEN -> X-CSRF-Token
                header_name = key[5:].replace("_", "-").title()
                headers[header_name] = value
            elif key in ("CONTENT_TYPE", "CONTENT_LENGTH"):
                header_name = key.replace("_", "-").title()
                headers[header_name] = value

        self._headers_cache = headers
        return headers
