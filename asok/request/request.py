from __future__ import annotations

import json
import re
import secrets
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, Optional, Union
from urllib.parse import parse_qsl, unquote

if TYPE_CHECKING:
    from asok.core.asok import Asok

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


def _is_safe_csrf_token(token: Optional[str]) -> bool:
    if not token or not (10 <= len(token) <= 128):
        return False
    return all(c.isalnum() or c in "_-+=" for c in token)


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
        self.raw_query_string: str = environ.get("QUERY_STRING", "")
        self.query_string: str = unquote(self.raw_query_string)
        self._init_state()
        self._init_cookies(environ.get("HTTP_COOKIE", ""))
        self._init_csrf(environ)
        self._init_locale(environ)
        self._init_flash()
        self._init_body(environ)

    def _init_state(self) -> None:
        self.body: bytes = b""
        self._csrf_verified = False
        self.args: QueryDict = QueryDict(parse_qsl(self.raw_query_string))
        self.form: dict[str, str] = dict(self.args)
        self.post_form: dict[str, str] = {}
        self.files: dict[str, UploadedFile] = {}
        self.all_files: list[UploadedFile] = []
        self._files_multi: dict[str, list[tuple[str, UploadedFile]]] = {}
        self.json_body: Optional[Any] = None
        self.content_type: str = "text/html; charset=utf-8"
        self.status: str = "200 OK"
        self.params: dict[str, str] = {}
        self.response_headers: list[tuple[str, str]] = []
        self._user_instance = None
        self._auth_resolved = False
        self._session = None
        self.env = _Env()
        self.meta: Metadata = Metadata()
        self.page_id: Optional[str] = None
        self.scoped_assets: dict[str, Optional[str]] = {"css": None, "js": None}
        self._nonce: Optional[str] = None
        self._body_rejected = False

    def _init_cookies(self, cookie_header: str) -> None:
        # SECURITY: RFC 6265 cookie parsing with URL decoding
        self.cookies_dict: dict[str, str] = {}
        if not cookie_header:
            return

        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header)
            for key, morsel in cookies.items():
                self.cookies_dict[key] = unquote(morsel.value)
        except Exception:
            self._parse_cookies_fallback(cookie_header)

    def _parse_cookies_fallback(self, cookie_header: str) -> None:
        import logging

        logging.getLogger("asok.request").warning(
            "Failed to parse cookies with SimpleCookie, using fallback"
        )
        for pair in cookie_header.split(";"):
            self._set_cookie_from_pair(pair.strip())

    def _set_cookie_from_pair(self, pair: str) -> None:
        if "=" not in pair:
            return
        k, v = pair.split("=", 1)
        val = v.strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1]
        self.cookies_dict[k.strip()] = unquote(val)

    def _init_csrf(self, environ: dict[str, Any]) -> None:
        self._csrf_cookie_name = "asok_csrf"
        token = self.cookies_dict.get(self._csrf_cookie_name)
        if _is_safe_csrf_token(token):
            self.csrf_token_value = token
        else:
            self.csrf_token_value = secrets.token_hex(32)
            if "asok.csrf_token_new" not in environ:
                environ["asok.csrf_token_new"] = self.csrf_token_value

    def _init_locale(self, environ: dict[str, Any]) -> None:
        self.lang = self.form.get("lang", self.cookies_dict.get("asok_lang"))
        if not self.lang:
            accept_lang = environ.get("HTTP_ACCEPT_LANGUAGE", "")
            if accept_lang:
                self.lang = accept_lang.split(",")[0].split("-")[0].split(";")[0]
        app_ref = environ.get("asok.app")
        self.lang = self.lang or (app_ref.config.get("LOCALE") if app_ref else "en")

    def _init_flash(self) -> None:
        self._flash_cookie_name = "asok_flash"
        self.flashed_messages: list[dict[str, str]] = []
        self._new_flashes: list[dict[str, str]] = []
        self._new_flashes_consumed: bool = False
        raw_flash = self.cookies_dict.get(self._flash_cookie_name)
        if not raw_flash:
            return
        try:
            # Cookie payload is HMAC-signed; legacy unsigned cookies are ignored.
            unsigned = self._unsign(unquote(raw_flash))
            if unsigned:
                self.flashed_messages = json.loads(unsigned)
        except Exception as e:
            import logging

            logging.getLogger("asok.security").warning(
                "Failed to deserialize flash messages: %s", e
            )

    def _content_length(self, environ: dict[str, Any]) -> int:
        try:
            return int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            return 0

    def _max_body_size(self, environ: dict[str, Any]) -> int:
        app_ref = environ.get("asok.app")
        default = 10 * 1024 * 1024
        if app_ref:
            return app_ref.config.get("MAX_CONTENT_LENGTH", default)
        return default

    def _init_body(self, environ: dict[str, Any]) -> None:
        content_length = self._content_length(environ)
        if content_length > self._max_body_size(environ):
            self.status = "413 Payload Too Large"
            self._body_rejected = True
            return
        if content_length <= 0:
            return
        self.body = environ["wsgi.input"].read(content_length)
        self._parse_body(environ.get("CONTENT_TYPE", ""))

    def _parse_body(self, enc_content_type: str) -> None:
        if "application/x-www-form-urlencoded" in enc_content_type:
            body_dict = dict(parse_qsl(self.body.decode("utf-8")))
            self.form.update(body_dict)
            self.post_form.update(body_dict)
        elif "application/json" in enc_content_type:
            self._parse_json_body()
            if isinstance(self.json_body, dict):
                self.post_form.update(self.json_body)
        elif "multipart/form-data" in enc_content_type:
            self._parse_multipart_body(enc_content_type)

    def _parse_json_body(self) -> None:
        try:
            # SECURITY: MAX_CONTENT_LENGTH caps size; RecursionError stops deep-JSON DoS.
            self.json_body = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, RecursionError) as e:
            if isinstance(e, RecursionError):
                import logging

                logging.getLogger("asok.security").warning(
                    "Rejected deeply nested JSON payload (possible DoS attempt)"
                )

    def _extract_boundary(self, enc_content_type: str) -> Optional[bytes]:
        # SECURITY: RFC 2046 — boundary may be quoted.
        match = re.search(r'boundary=([^;\s]+|"[^"]+")', enc_content_type)
        if not match:
            return None
        raw = match.group(1).strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return raw.encode()

    def _parse_multipart_body(self, enc_content_type: str) -> None:
        boundary = self._extract_boundary(enc_content_type)
        if not boundary:
            return
        for part in self.body.split(b"--" + boundary):
            self._handle_multipart_part(part)

    def _handle_multipart_part(self, part: bytes) -> None:
        if not self._is_valid_multipart_part(part):
            return
        head, payload = self._split_multipart_part(part)
        cdisp = self._content_disposition(head)
        name = self._multipart_field_name(cdisp)
        if name:
            self._store_multipart_field(name, cdisp, payload)

    @staticmethod
    def _is_valid_multipart_part(part: bytes) -> bool:
        if not part or part in (b"--\r\n", b"--"):
            return False
        return b"\r\n\r\n" in part

    @staticmethod
    def _split_multipart_part(part: bytes) -> tuple[bytes, bytes]:
        head, payload = part.split(b"\r\n\r\n", 1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        return head, payload

    @staticmethod
    def _multipart_field_name(cdisp: str) -> Optional[str]:
        if not cdisp or "name=" not in cdisp:
            return None
        match = _RE_NAME.search(cdisp)
        return match.group(1) if match else None

    def _content_disposition(self, head: bytes) -> str:
        head_str = head.decode("utf-8", errors="ignore")
        for line in head_str.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                return line
        return ""

    def _store_multipart_field(self, name: str, cdisp: str, payload: bytes) -> None:
        filename_match = _RE_FILENAME.search(cdisp)
        if not filename_match:
            val = payload.decode("utf-8", errors="ignore")
            self.form[name] = val
            self.post_form[name] = val
            return
        raw_filename = filename_match.group(1)
        if not raw_filename:
            return
        uploaded = UploadedFile(secure_filename(raw_filename), payload)
        self.files[name] = uploaded
        self.all_files.append(uploaded)
        self._files_multi.setdefault(name, []).append((name, uploaded))

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
        if not forwarded or not self._is_proxy_trusted(remote_addr):
            return remote_addr
        return forwarded.split(",")[-1].strip()

    def _is_proxy_trusted(self, remote_addr: str) -> bool:
        app_ref = self.environ.get("asok.app")
        trusted = app_ref.config.get("TRUSTED_PROXIES") if app_ref else None
        if trusted is None:
            return False
        return trusted == "*" or remote_addr in trusted

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

    _LANG_TO_COUNTRY = {
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

    _UNKNOWN_COUNTRY = {
        "iso": "Unknown",
        "name": "Unknown",
        "dial_code": "",
        "flag": "🌐",
        "capital": "Unknown",
        "continent": "Unknown",
        "currency": "Unknown",
        "languages": "Unknown",
    }

    @property
    def geo(self) -> dict[str, Any]:
        """Get comprehensive geographic information for the current request.

        Combines IP-based location (city, lat, lon) with rich country metadata
        (flag, currency, timezone, etc.).
        """
        cache = self.__dict__.setdefault("_geo_cache", None)
        if cache:
            return cache
        loc = IPLocation.get_instance().lookup(self.ip)
        from asok.utils.geo import Countries

        country_code = self._resolve_country_code(loc)
        country_info = Countries.get(country_code) or self._UNKNOWN_COUNTRY
        city = self._resolve_city(loc, country_info)
        geo_data = {
            "ip": self.ip,
            "city": city,
            "country": country_code,
            "lat": loc.get("lat", 0.0),
            "lon": loc.get("lon", 0.0),
            **country_info,
        }
        geo_data["timezone"] = Countries.get_timezone(geo_data["iso"])
        self.__dict__["_geo_cache"] = geo_data
        return geo_data

    def _resolve_country_code(self, loc: dict[str, Any]) -> str:
        code = loc.get("country", "")
        if code and code != "Unknown":
            return code
        return self._LANG_TO_COUNTRY.get(self.lang, "US")

    @staticmethod
    def _resolve_city(loc: dict[str, Any], country_info: dict[str, Any]) -> str:
        city = loc.get("city", "Unknown")
        if city and city != "Unknown":
            return city
        capital = country_info.get("capital", "Unknown")
        return capital if capital != "Unknown" else "Unknown"

    @property
    def location(self) -> dict[str, Any]:
        """Legacy alias for request.geo (for backward compatibility)."""
        return self.geo

    def require_auth(self, redirect_url: str = "/login") -> None:
        """Ensure the user is authenticated, else redirect to login with a 'next' parameter."""
        if self.is_authenticated:
            return
        from urllib.parse import quote

        next_url = self._safe_next_url()
        next_encoded = quote(next_url, safe="/?&=")
        separator = "&" if "?" in redirect_url else "?"
        raise RedirectException(f"{redirect_url}{separator}next={next_encoded}")

    def _safe_next_url(self) -> str:
        # SECURITY: only relative URLs may be carried in `next` (no open redirect).
        next_url = self.path
        if self.raw_query_string:
            next_url += "?" + self.raw_query_string
        if next_url and ("://" in next_url or next_url.startswith("//")):
            import logging

            logging.getLogger("asok.security").warning(
                "Open redirect attempt blocked: %s", next_url
            )
            return "/"
        return next_url

    @property
    def app(self) -> Asok:
        """Get the active Asok application instance for this request (like Flask's current_app)."""
        app_ref = self.environ.get("asok.app")
        # Assert type for runtime/static analysis
        return app_ref  # type: ignore[return-value]

    @property
    def scheme(self) -> str:
        """The URL scheme (http or https)."""
        remote_addr = self.environ.get("REMOTE_ADDR", "")
        if self._is_proxy_trusted(remote_addr):
            forwarded_proto = self.environ.get("HTTP_X_FORWARDED_PROTO")
            if forwarded_proto:
                return forwarded_proto.lower()
        return self.environ.get("wsgi.url_scheme", "http")

    @property
    def host(self) -> str:
        """The Host header value with validation against injection attacks."""
        host = self.environ.get("HTTP_HOST", "localhost")
        # SECURITY: reject control chars to block injection attacks.
        if any(c in host for c in ("\r", "\n", "\t", " ", "\x00")):
            import logging

            logging.getLogger("asok.security").warning(
                "Malformed Host header detected: %r", host
            )
            return "localhost"
        return self._strip_host_port(host)

    @staticmethod
    def _strip_host_port(host: str) -> str:
        if host.startswith("["):
            if "]:" in host:
                return host.split("]:")[0] + "]"
            return host
        if ":" in host:
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
