from __future__ import annotations

import json
import re
import secrets
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any, Iterable, Optional, Union
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


class FlashMessage(tuple):
    """Represents a flash message as a (category, message) tuple with backward-compatible dict/attribute access."""

    def __new__(cls, category: str, message: str):
        return super().__new__(cls, (str(category or "info"), str(message or "")))

    @property
    def category(self) -> str:
        return super().__getitem__(0)

    @property
    def message(self) -> str:
        return super().__getitem__(1)

    def __str__(self) -> str:
        return super().__getitem__(1)

    def __getitem__(self, item: Any) -> Any:
        if item == "category" or item == 0:
            return super().__getitem__(0)
        if item == "message" or item == 1:
            return super().__getitem__(1)
        return super().__getitem__(item)

    def get(self, key: str, default: Any = None) -> Any:
        if key == "category":
            return super().__getitem__(0)
        if key == "message":
            return super().__getitem__(1)
        return default


def _parse_flash_item(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        return str(item.get("category", "info")), str(item.get("message", ""))
    return str(item[0]), str(item[1])


def _format_flash_messages(
    raw: list[Any],
    with_categories: bool,
    allowed: Optional[set[str]],
) -> list[Any]:
    out: list[Any] = []
    for x in raw:
        c, m = _parse_flash_item(x)
        if allowed and c not in allowed:
            continue
        val = FlashMessage(c, m) if with_categories else m
        out.append(val)
    return out


_STANDARD_FLASH_CATEGORIES = frozenset(
    {"info", "success", "error", "warning", "danger", "alert", "notice", "debug"}
)


def _resolve_flash_args(arg1: Any, arg2: Optional[Any]) -> tuple[str, str]:
    if arg2 is None:
        return "info", str(arg1)
    s1, s2 = str(arg1), str(arg2)
    if (
        s2.lower() in _STANDARD_FLASH_CATEGORIES
        and s1.lower() not in _STANDARD_FLASH_CATEGORIES
    ):
        return s2.lower(), s1
    return s1, s2


def _is_form_target(target: Any) -> bool:
    from asok.forms import Form

    if isinstance(target, Form):
        return True
    return isinstance(target, type) and issubclass(target, Form)


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
        self._json_error: Optional[Exception] = None

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

    @property
    def cookies(self) -> dict[str, str]:
        """A dictionary of all parsed request cookies."""
        return self.cookies_dict

    def cookie(self, name: str, default: Any = None) -> Any:
        """Get a single request cookie by name, or default if absent."""
        return self.cookies_dict.get(name, default)

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
                "Failed to deserialize flash messages: %s",
                e,
                extra={"event": "flash_deserialize_failed", "error": str(e)},
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
            # SECURITY: MAX_CONTENT_LENGTH caps size; depth limit stops deep-JSON DoS.
            raw_text = self.body.decode("utf-8")
            from ..utils.security import is_safe_json_depth

            if not is_safe_json_depth(raw_text, max_depth=100):
                import logging

                logging.getLogger("asok.security").warning(
                    "Rejected deeply nested JSON payload exceeding maximum depth (possible DoS attempt)",
                    extra={
                        "event": "json_depth_exceeded",
                        "path": getattr(self, "path", ""),
                    },
                )
                self.json_body = None
                return
            self.json_body = json.loads(raw_text)
        except (json.JSONDecodeError, RecursionError, ValueError) as e:
            if isinstance(e, RecursionError):
                import logging

                logging.getLogger("asok.security").warning(
                    "Rejected deeply nested JSON payload (possible DoS attempt)",
                    extra={
                        "event": "json_recursion_error",
                        "path": getattr(self, "path", ""),
                    },
                )
            self.json_body = None
            self._json_error = e

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

    def flash(self, category_or_message: str, message: Optional[str] = None) -> None:
        """Store a temporary message to be displayed on the next page rendering.

        Supports:
            - Asok style: request.flash("success", "Saved successfully!")
            - Flask style: request.flash("Saved successfully!", "success")
            - Single arg: request.flash("Saved successfully!")  # default: 'info'
        """
        cat, msg = _resolve_flash_args(category_or_message, message)
        self._new_flashes.append({"category": cat, "message": msg})

    def get_flashed_messages(
        self,
        with_categories: bool = True,
        category_filter: Optional[Iterable[str]] = None,
    ) -> list[Any]:
        """Retrieve all flash messages.

        Supports Flask-style tuple unpacking:
            {% for category, message in get_flashed_messages() %}
                <div class="alert alert-{{ category }}">{{ message }}</div>
            {% endfor %}

        As well as direct property access (backward-compatible):
            {% for f in get_flashed_messages() %}{{ f.message }}{% endfor %}

        And plain string list when with_categories=False:
            {% for message in get_flashed_messages(with_categories=False) %}
        """
        if self._new_flashes:
            self._new_flashes_consumed = True

        allowed = set(category_filter) if category_filter else None
        return _format_flash_messages(
            self.flashed_messages + self._new_flashes,
            with_categories,
            allowed,
        )

    def old(self, name: str, default: Any = "") -> Any:
        """Retrieve a submitted or session-flashed form value for repopulating HTML inputs."""
        if self.form and name in self.form:
            return self.form.get(name, default)
        session_old = (
            self.session.get("_old_input", {}) if hasattr(self, "session") else {}
        )
        return session_old.get(name, default)

    @property
    def errors(self) -> dict[str, str]:
        """Retrieve active validation errors."""
        cache = getattr(self, "_cached_errors", None)
        if cache is not None:
            return cache
        return self.session.get("_errors", {}) if hasattr(self, "session") else {}

    def error(self, name: str) -> str:
        """Retrieve the validation error message for a specific field."""
        return self.errors.get(name, "")

    def _validate_form_target(self, target: Any, csrf: bool, raise_error: bool) -> Any:
        from asok.forms import Form

        if isinstance(target, type) and issubclass(target, Form):
            form = target(self)
            form.validate(csrf=csrf, raise_error=raise_error)
            return form
        target.bind(self)
        target.validate(csrf=csrf, raise_error=raise_error)
        return target

    @property
    def is_json(self) -> bool:
        """Check if incoming request has a JSON Content-Type."""
        ct = self.environ.get("CONTENT_TYPE", "")
        return "application/json" in ct

    def _resolve_json_payload(self) -> dict[str, Any]:
        if getattr(self, "_json_error", None) is not None:
            from asok.exceptions import ValidationError

            raise ValidationError(
                f"Malformed JSON in request body: {self._json_error}",
                errors={"_json": [f"Malformed JSON: {self._json_error}"]},
            )
        json_data = self.get_json()
        if json_data is not None and not isinstance(json_data, dict):
            from asok.exceptions import ValidationError

            raise ValidationError(
                f"Expected a JSON object (dict) in request body, got {type(json_data).__name__}",
                errors={"_json": ["JSON payload must be an object"]},
            )
        return json_data if isinstance(json_data, dict) else {}

    def _resolve_validation_payload(
        self, data: Optional[dict[str, Any]]
    ) -> dict[str, Any]:
        if data is not None:
            return data
        if self.is_json:
            return self._resolve_json_payload()
        return self.form or {}

    def _maybe_verify_csrf(self, csrf: bool) -> None:
        if csrf and self.method in ("POST", "PUT", "PATCH", "DELETE"):
            self.verify_csrf()

    def _run_rule_validation(self, rules: Any, data: Any, raise_error: bool) -> Any:
        from asok.exceptions import ValidationError
        from asok.validation import Validator

        payload = self._resolve_validation_payload(data)
        v = Validator(payload, self.files, translate=self.__)
        if not v.rules(rules):
            if raise_error:
                raise ValidationError("Validation failed", errors=v.errors)
            return False
        return payload

    def validate(
        self,
        rules_or_form: Any,
        data: Optional[dict[str, Any]] = None,
        csrf: bool = True,
        raise_error: bool = True,
    ) -> Any:
        """Validate request input against validation rules or a Form class.

        Usage:
            data = request.validate({"email": "required|email", "name": "required"})
            # or
            form = request.validate(ContactForm)
        """
        if _is_form_target(rules_or_form):
            return self._validate_form_target(rules_or_form, csrf, raise_error)
        if not isinstance(rules_or_form, dict):
            raise TypeError(
                f"request.validate() expects a dict of rules or a Form class, got {type(rules_or_form).__name__}"
            )
        self._maybe_verify_csrf(csrf)
        return self._run_rule_validation(rules_or_form, data, raise_error)

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
        """Enforce authentication, aborting with 401 if not logged in.

        If ``app.config["LOGIN_MESSAGE"]`` is set (and non-empty), it is
        flashed as an ``"info"`` message *before* the 401 is raised so that
        the login page (or any error handler) can surface the reason for the
        redirect to the user.  Set ``LOGIN_MESSAGE`` to ``None`` or ``""``
        to suppress the flash entirely.
        """
        if not self.user:
            app_ref = self.environ.get("asok.app")
            login_message: Optional[str] = (
                app_ref.config.get(
                    "LOGIN_MESSAGE", "Please log in to access this page."
                )
                if app_ref
                else "Please log in to access this page."
            )
            if login_message:
                self.flash("info", login_message)
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

    def _is_secure_cookie_required(self, app_ref: Optional[Any]) -> bool:
        return self.scheme == "https" or bool(
            app_ref and not app_ref.config.get("DEBUG")
        )

    _VALID_SAMESITE = frozenset({"Strict", "Lax", "None"})

    def _resolve_session_samesite(self, app_ref: Optional[Any]) -> str:
        raw = str(
            app_ref.config.get("SESSION_SAMESITE", "Lax") if app_ref else "Lax"
        ).strip()
        raw = raw.replace("\r", "").replace("\n", "")
        title_val = raw.capitalize()
        return title_val if title_val in self._VALID_SAMESITE else "Lax"

    def _session_cookie(self, value: str, max_age: Optional[int] = None) -> str:
        """Formats the session cookie header."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        samesite = self._resolve_session_samesite(app_ref)
        cookie = f"asok_session={value}; HttpOnly; Path=/; SameSite={samesite}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        if self._is_secure_cookie_required(app_ref) or samesite == "None":
            cookie += "; Secure"
        return cookie

    def _format_translation(self, text: str, kwargs: dict[str, Any], key: str) -> str:
        try:
            return text.format(**kwargs)
        except KeyError as e:
            import logging

            logging.getLogger("asok.i18n").warning(
                "Missing translation placeholder %s in key '%s' for locale '%s'",
                e,
                key,
                self.lang,
            )
        except Exception:
            pass
        return text

    def __(self, key: str, **kwargs: Any) -> str:
        """Translate a key using the current request language."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "locales"):
            return key
        lang_dict = app_ref.locales.get(self.lang, app_ref.locales.get("en", {}))
        text = lang_dict.get(key, key)
        return self._format_translation(text, kwargs, key) if kwargs else text

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
        return self._resolve_forwarded_ip(forwarded, remote_addr)

    def _resolve_forwarded_ip(self, forwarded: str, remote_addr: str) -> str:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        return hops[0] if hops else remote_addr

    def _is_proxy_trusted(self, remote_addr: str) -> bool:
        app_ref = self.environ.get("asok.app")
        trusted = app_ref.config.get("TRUSTED_PROXIES") if app_ref else None
        if trusted is None:
            return False
        if isinstance(trusted, str):
            return trusted == "*" or remote_addr == trusted
        return remote_addr in trusted

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
        lang_key = self.lang or ""
        return self._LANG_TO_COUNTRY.get(lang_key, "US")

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

        # Flash the login message from app config (same as login_required does),
        # so the login page can display the reason for the redirect.
        app_ref = self.environ.get("asok.app")
        login_message: Optional[str] = (
            app_ref.config.get("LOGIN_MESSAGE", "Please log in to access this page.")
            if app_ref
            else "Please log in to access this page."
        )
        if login_message:
            self.flash("info", login_message)

        next_url = self._safe_next_url()
        next_encoded = quote(next_url, safe="/?&=")
        separator = "&" if "?" in redirect_url else "?"
        raise RedirectException(f"{redirect_url}{separator}next={next_encoded}")

    def _safe_next_url(self) -> str:
        # SECURITY: only relative URLs may be carried in `next` (no open redirect).
        from asok.utils.security import is_safe_url

        next_url = self.path
        if self.raw_query_string:
            next_url += "?" + self.raw_query_string
        if next_url and not is_safe_url(next_url):
            import logging

            logging.getLogger("asok.security").warning(
                "Open redirect attempt blocked: %s",
                next_url,
                extra={
                    "event": "open_redirect_blocked",
                    "blocked_url": next_url,
                    "path": getattr(self, "path", ""),
                },
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
        """The Host header value (including port if present) with ALLOWED_HOSTS and injection validation."""
        cached = self.__dict__.get("_host_cache")
        if cached is not None:
            return cached
        raw_host = self.environ.get("HTTP_HOST") or self._fallback_environ_host()
        if self._is_malformed_host(raw_host):
            resolved = "localhost"
        else:
            resolved = self._validate_allowed_host(raw_host)
        self.__dict__["_host_cache"] = resolved
        return resolved

    def _validate_allowed_host(self, raw_host: str) -> str:
        app_ref = self.environ.get("asok.app")
        if not app_ref:
            return raw_host
        allowed_hosts = app_ref.config.get("ALLOWED_HOSTS")
        if not allowed_hosts or allowed_hosts == "*" or allowed_hosts == ["*"]:
            return raw_host
        return self._check_host_against_allowed(raw_host, allowed_hosts, app_ref)

    def _is_host_matched(self, allowed: str, raw_host: str, host_name: str) -> bool:
        if allowed in ("*", raw_host, host_name):
            return True
        return bool(
            allowed.startswith(".")
            and (host_name.endswith(allowed) or host_name == allowed[1:])
        )

    def _check_host_against_allowed(
        self, raw_host: str, allowed_hosts: Any, app_ref: Any
    ) -> str:
        host_name = self._strip_host_port(raw_host)
        hosts_list = (
            [allowed_hosts] if isinstance(allowed_hosts, str) else allowed_hosts
        )
        if any(self._is_host_matched(h, raw_host, host_name) for h in hosts_list):
            return raw_host

        import logging

        logging.getLogger("asok.security").warning(
            "Host header poisoning attempt blocked: Host %r not in ALLOWED_HOSTS %r",
            raw_host,
            allowed_hosts,
            extra={
                "event": "host_poisoning_blocked",
                "host": raw_host,
                "allowed_hosts": str(allowed_hosts),
            },
        )
        return self._fallback_allowed_host(app_ref, hosts_list)

    @staticmethod
    def _app_url_netloc(app_ref: Any) -> str:
        app_url = app_ref.config.get("APP_URL")
        if not app_url:
            return ""
        from urllib.parse import urlsplit

        parsed = urlsplit(app_url if "://" in app_url else f"http://{app_url}")
        return parsed.netloc or ""

    @classmethod
    def _fallback_allowed_host(cls, app_ref: Any, hosts_list: list[str]) -> str:
        netloc = cls._app_url_netloc(app_ref)
        if netloc:
            return netloc
        return next((h for h in hosts_list if h and h != "*"), "localhost")

    def _fallback_environ_host(self) -> str:
        server_name = self.environ.get("SERVER_NAME", "localhost")
        server_port = self.environ.get("SERVER_PORT", "")
        if server_port and server_port not in ("80", "443"):
            return f"{server_name}:{server_port}"
        return server_name

    @staticmethod
    def _is_malformed_host(raw_host: str) -> bool:
        if any(c in raw_host for c in ("\r", "\n", "\t", " ", "\x00", ";")):
            import logging

            logging.getLogger("asok.security").warning(
                "Malformed Host header detected: %r",
                raw_host,
                extra={"event": "malformed_host_detected", "host": raw_host},
            )
            return True
        return False

    @property
    def hostname(self) -> str:
        """The Host header value without port."""
        return self._strip_host_port(self.host)

    @property
    def host_url(self) -> str:
        """The scheme and host URL with a trailing slash (e.g. 'http://localhost:8000/')."""
        return f"{self.scheme}://{self.host}/"

    @property
    def base_url(self) -> str:
        """The full URL excluding query string (e.g. 'http://localhost:8000/path')."""
        path = self.path if self.path.startswith("/") else f"/{self.path}"
        return f"{self.scheme}://{self.host}{path}"

    @property
    def url(self) -> str:
        """The full URL including scheme, host, path, and query string."""
        if self.raw_query_string:
            return f"{self.base_url}?{self.raw_query_string}"
        return self.base_url

    @property
    def full_url(self) -> str:
        """Alias for request.url."""
        return self.url

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
