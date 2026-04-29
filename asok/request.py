from __future__ import annotations

import email
import email.policy
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import time
from typing import TYPE_CHECKING, Any, Iterator, Optional, Type, TypeVar, Union
from urllib.parse import parse_qsl, unquote

from .auth import BearerToken, MagicLink, OAuth
from .exceptions import (
    AbortException,  # noqa: F401 — re-export for compat
    RedirectException,
)
from .orm import MODELS_REGISTRY
from .session import Session
from .templates import SafeString, render_block_string, render_template_string
from .utils.geo import IPLocation
from .utils.image import is_image, optimize_image
from .utils.security import is_safe_url, secure_filename

if TYPE_CHECKING:
    from .core import Asok
    from .forms import Form


class QueryDict(dict):
    """A dict that also keeps all values for repeated keys (e.g. ``?a=1&a=2``)."""

    def __init__(self, pairs: list[tuple[str, str]]):
        super().__init__(pairs)
        self._lists: dict[str, list[str]] = {}
        for k, v in pairs:
            self._lists.setdefault(k, []).append(v)

    def getlist(self, key: str, default: Optional[list[str]] = None) -> list[str]:
        """Return all values for *key*, or *default* if absent."""
        return self._lists.get(key, default if default is not None else [])


# Pre-compiled regex for multipart parsing
_RE_NAME = re.compile(r'name="([^"]+)"')
_RE_FILENAME = re.compile(r'filename="([^"]+)"')


class _Env:
    """Helper to access environment variables with automatic type casting."""

    @staticmethod
    def _cast(val: Any) -> Any:
        if isinstance(val, str):
            lv = val.lower()
            if lv == "true":
                return True
            if lv == "false":
                return False
            if lv == "null":
                return None
        return val

    def __call__(self, key: str, default: Any = None) -> Any:
        """Get env var as property: request.env('KEY', default)."""
        val = os.environ.get(key)
        return self._cast(val) if val is not None else default

    def __getitem__(self, key: str) -> Any:
        """Get env var as index: request.env['KEY']. Raises KeyError if missing."""
        val = os.environ.get(key)
        if val is None:
            raise KeyError(key)
        return self._cast(val)

    def get(self, key: str, default: Any = None) -> Any:
        """Alias for calling the object."""
        return self(key, default)


_RE_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitize_filename(filename: str) -> str:
    """Sanitize an uploaded filename to prevent path traversal and unsafe characters."""
    # Strip any path components (Windows or Unix)
    filename = filename.replace("\\", "/")
    filename = filename.split("/")[-1]
    # Remove unsafe characters
    filename = _RE_UNSAFE_FILENAME.sub("_", filename)
    # Strip leading dots/spaces to prevent hidden files
    filename = filename.lstrip(". ")
    # Fallback for empty names
    if not filename:
        filename = "upload"
    return filename


class UploadedFile:
    """Wrapper for a file uploaded via multipart/form-data."""

    def __init__(self, filename: str, content: bytes):
        self.filename: str = _sanitize_filename(filename)
        self.content: bytes = content
        self.size: int = len(content)

    def read(self, size: int = -1) -> bytes:
        """Read and return up to *size* bytes of the file content.

        Note: Since Asok stores uploaded content in memory, this returns the entire
        buffer if no size is specified, but does not track a cursor like a real file.
        Use .file.read() if cursor management is needed.
        """
        if size == -1:
            return self.content
        return self.content[:size]

    @property
    def file(self) -> io.BytesIO:
        """Return a file-like BytesIO stream of the content."""
        return io.BytesIO(self.content)

    def save(self, destination: str) -> str:
        """Save the uploaded file to disk.

        Args:
            destination: Path relative to project root or absolute path.

        Returns:
            The absolute path where the file was saved.
        """
        base_dir = os.path.abspath(os.path.join(os.getcwd(), "src/partials/uploads"))
        if not os.path.isabs(destination):
            destination = os.path.join(base_dir, destination)

        dest = os.path.abspath(destination)
        try:
            common = os.path.commonpath([dest, base_dir])
        except ValueError:
            common = ""
        if common != base_dir:
            raise ValueError(f"Path traversal blocked: {destination}")

        dest_dir = os.path.dirname(dest)
        os.makedirs(dest_dir, exist_ok=True)

        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            counter = 1
            while os.path.exists(f"{base}_{counter}{ext}"):
                counter += 1
            dest = f"{base}_{counter}{ext}"

        with open(dest, "wb") as f:
            f.write(self.content)

        # Optimization hook
        if os.environ.get("IMAGE_OPTIMIZATION") == "true" and is_image(dest):
            keep = os.environ.get("IMAGE_KEEP_ORIGINAL", "true").lower() != "false"
            optimize_image(dest, keep_original=keep)

        self.filename = os.path.basename(dest)
        return dest

    def __getitem__(self, key: str) -> Union[str, bytes]:
        return {"filename": self.filename, "content": self.content}[key]


class UserAgent:
    """Lightweight parser for identifying browser, OS, and mobile status."""

    def __init__(self, ua_string: Optional[str]):
        self.raw: str = ua_string or ""
        self._parsed: bool = False
        self._name: str = "Unknown"
        self._os: str = "Unknown"
        self._is_mobile: bool = False

    def _parse(self) -> None:
        if self._parsed:
            return
        ua = self.raw
        # Order matters: Edge/Opera/Chrome contain Safari; Edge/Chrome contain Chrome
        if "Edg/" in ua:
            self._name = "Edge"
        elif "OPR/" in ua or "Opera" in ua:
            self._name = "Opera"
        elif "MSIE" in ua or "Trident/" in ua:
            self._name = "Internet Explorer"
        elif "Firefox/" in ua:
            self._name = "Firefox"
        elif "Chrome/" in ua:
            self._name = "Chrome"
        elif "Safari/" in ua:
            self._name = "Safari"

        # OS detection
        if "Windows" in ua:
            self._os = "Windows"
        elif "iPhone" in ua or "iPad" in ua:
            self._os = "iOS"
        elif "Android" in ua:
            self._os = "Android"
        elif "Mac OS X" in ua:
            self._os = "macOS"
        elif "Linux" in ua:
            self._os = "Linux"

        # Mobile detection
        self._is_mobile = any(
            x in ua.lower() for x in ["mobile", "android", "iphone", "ipad"]
        )
        self._parsed = True

    @property
    def name(self) -> str:
        """The identified browser name (e.g., 'Chrome', 'Firefox')."""
        self._parse()
        return self._name

    @property
    def os(self) -> str:
        """The identified operating system (e.g., 'Windows', 'iOS')."""
        self._parse()
        return self._os

    @property
    def is_mobile(self) -> bool:
        """True if the request originates from a mobile device."""
        self._parse()
        return self._is_mobile

    def __str__(self) -> str:
        return self.raw


class Metadata:
    """SEO and social metadata manager. Accessible via request.meta."""

    def __init__(self):
        self._title: Optional[str] = None
        self._description: Optional[str] = None
        self._items: list[tuple[str, str, str, dict[str, Any]]] = []

    def title(self, val: str) -> str:
        """Set page title."""
        self._title = val
        return ""

    def description(self, val: str) -> str:
        """Set meta description."""
        self._description = val
        return ""

    def name(self, name: str, content: str, **kwargs: Any) -> str:
        """Add a <meta name="..." content="..."> tag."""
        self._items = [i for i in self._items if not (i[0] == "name" and i[1] == name)]
        self._items.append(("name", name, content, kwargs))
        return ""

    def property(self, prop: str, content: str, **kwargs: Any) -> str:
        """Add a <meta property="..." content="..."> tag (OpenGraph)."""
        self._items = [
            i for i in self._items if not (i[0] == "property" and i[1] == prop)
        ]
        self._items.append(("property", prop, content, kwargs))
        return ""

    def link(self, rel: str, href: str, **kwargs: Any) -> str:
        """Add a <link rel="..." href="..."> tag."""
        self._items.append(("link", rel, href, kwargs))
        return ""

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "title" and not callable(value):
            self._title = value
        elif name == "description" and not callable(value):
            self._description = value
        else:
            super().__setattr__(name, value)

    def __str__(self) -> str:
        return ""


# Generic type for shared variable resolution
T = TypeVar("T")


class Request:
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

        self.cookies_dict = {}
        cookie_header = environ.get("HTTP_COOKIE", "")
        if cookie_header:
            for pair in cookie_header.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    self.cookies_dict[k.strip()] = v.strip()

        self._csrf_cookie_name = "asok_csrf"
        self.csrf_token_value = self.cookies_dict.get(self._csrf_cookie_name)
        if not self.csrf_token_value:
            self.csrf_token_value = secrets.token_hex(32)

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
            except Exception:
                pass

        try:
            content_length = int(environ.get("CONTENT_LENGTH", 0))
        except ValueError:
            content_length = 0

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

        if content_length > 0:
            self.body = environ["wsgi.input"].read(content_length)
            enc_content_type = environ.get("CONTENT_TYPE", "")

            if "application/x-www-form-urlencoded" in enc_content_type:
                self.form.update(dict(parse_qsl(self.body.decode("utf-8"))))
            elif "application/json" in enc_content_type:
                try:
                    self.json_body = json.loads(self.body.decode("utf-8"))
                except json.JSONDecodeError:
                    pass
            elif "multipart/form-data" in enc_content_type:
                mime_body = (
                    b"Content-Type: "
                    + enc_content_type.encode()
                    + b"\r\n\r\n"
                    + self.body
                )
                msg = email.message_from_bytes(mime_body, policy=email.policy.default)
                if msg.is_multipart():
                    for part in msg.iter_parts():
                        cdisp = part.get("Content-Disposition")
                        if cdisp and "name=" in cdisp:
                            name_match = _RE_NAME.search(cdisp)
                            filename_match = _RE_FILENAME.search(cdisp)
                            if name_match:
                                name = name_match.group(1)
                                payload = part.get_payload(decode=True)
                                if filename_match:
                                    raw_filename = filename_match.group(1)
                                    safe_filename = secure_filename(raw_filename)
                                    uploaded = UploadedFile(safe_filename, payload)
                                    self.files[name] = uploaded
                                    self.all_files.append(uploaded)
                                    self._files_multi.setdefault(name, []).append(
                                        (name, uploaded)
                                    )
                                else:
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

    def _resolve_template(self, filepath: str) -> tuple[str, str]:
        """Convert a template path to its content and identify the partials root."""
        root = self.environ.get("asok.root", os.getcwd())
        if (
            not os.path.isabs(filepath)
            and not filepath.startswith("src/")
            and hasattr(self, "_current_page_file")
            and self._current_page_file
        ):
            page_dir = os.path.dirname(self._current_page_file)
            path = os.path.join(page_dir, filepath)
        else:
            path = os.path.join(root, filepath)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        tpl_root = os.path.join(root, "src/partials")
        return content, tpl_root

    def share(self, name: str, value: Any = None) -> Union[str, Any]:
        """Set or get a shared variable bound to this request.

        If a value is provided, it is stored in the per-request cache.
        If no value is provided, it returns the shared value.
        """
        if value is not None:
            self.__dict__.setdefault("_shared_cache", {})[name] = value
            return ""
        return self.shared(name)

    def shared(
        self, name: str, expected_type: Optional[Type[T]] = None
    ) -> Union[T, Any]:
        """Get a shared variable bound to this request (cached per request).

        Variables registered via app.share() or request.share() are auto-resolved.
        Callables are invoked with the current request, and Form templates are bound.
        Providing an 'expected_type' enables IDE autocompletion for the returned object.
        """
        cache = self.__dict__.setdefault("_shared_cache", {})

        # 1. Try to find the value (already cached or in app defaults)
        val = cache.get(name)
        was_in_cache = name in cache

        if val is None and not was_in_cache:
            app_ref: Optional[Asok] = self.environ.get("asok.app")
            if not app_ref or name not in getattr(app_ref, "_shared", {}):
                return None
            val = app_ref._shared[name]

        # 2. Resolution logic
        from .forms import Form

        resolved = val
        if isinstance(val, Form) and getattr(val, "_is_template", False):
            resolved = val._bind(self)
        elif not isinstance(val, Form) and callable(val):
            # Resolve callables (functions or Form classes)
            resolved = val(self)

        # 3. Always update cache with the resolved version to ensure stability
        if resolved is not val or not was_in_cache:
            cache[name] = resolved

        return resolved

    def shared_form(self, name: str) -> Form:
        """Typed internal helper to get a shared form with full IDE autocompletion.

        Returns an instance of Form automatically bound to this request.
        """
        from .forms import Form

        return self.shared(name, Form)

    def _template_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """Assemble the standard context variables for template rendering."""
        ctx = {
            "request": self,
            "nonce": getattr(self, "nonce", ""),
            "__": self.__,
            "static": self.static,
            "get_flashed_messages": self.get_flashed_messages,
            "meta": self.meta,
        }

        # -- component() helper ----------------------------------------
        # Instance counter for stable CIDs, shared across all templates in this request
        if not hasattr(self, "_comp_counters"):
            self._comp_counters = {}

        def component(name, *args, **kwargs):
            """Instantiate and render a reactive component."""
            from .component import COMPONENTS_REGISTRY

            cls = COMPONENTS_REGISTRY.get(name)
            if cls is None:
                return SafeString(f"<!-- Component '{name}' not found -->")

            # Stable CID: explicit or auto (tpl--name--index)
            cid = kwargs.pop("cid", None)
            if cid is None:
                tpl_name = getattr(self, "_current_page_file", "global")
                tpl_name = os.path.basename(tpl_name).replace(".", "-")
                self._comp_counters[name] = self._comp_counters.get(name, 0) + 1
                cid = f"{tpl_name}--{name.lower()}--{self._comp_counters[name]}"

            app_ref = self.environ.get("asok.app")
            secret = (
                app_ref.config.get("SECRET_KEY", "dev-secret-key")
                if app_ref
                else "dev-secret-key"
            )

            # Try to restore from session (persists across page refreshes)
            sess = self.session
            saved_signed = sess.get(f"_comp_{cid}")

            slot = kwargs.pop("slot", None)
            if slot is not None:
                slot = SafeString(slot)

            if saved_signed:
                try:
                    instance = cls._from_signed_state(saved_signed, secret, cid=cid)
                    if instance is not None:
                        instance._slot = slot
                        return SafeString(str(instance))
                except Exception:
                    pass

            # Fresh instance
            if args:
                state_keys = [
                    k
                    for k in cls.__dict__
                    if not k.startswith("_") and not callable(cls.__dict__[k])
                ]
                for i, val in enumerate(args):
                    if i < len(state_keys):
                        kwargs.setdefault(state_keys[i], val)

            instance = cls(_cid=cid, **kwargs)
            instance._slot = slot
            # Save initial state so it survives a refresh
            signed_state = instance._sign_state(secret)
            sess[f"_comp_{cid}"] = signed_state
            return SafeString(str(instance))

        ctx["component"] = component

        # Inject all app.share()d variables, bound per-request and cached
        app_ref = self.environ.get("asok.app")
        if app_ref and getattr(app_ref, "_shared", None):
            for name in app_ref._shared:
                try:
                    ctx[name] = self.shared(name)
                except Exception:
                    pass

        # Inject per-request shared variables (manual share)
        cache = self.__dict__.get("_shared_cache", {})
        ctx.update(cache)

        ctx.update(context)
        return ctx

    def html(self, filepath: str, **context: Any) -> str:
        """Render an HTML template and return the result as a string."""
        # Auto-detect block request (from data-block JS swap)
        block_header = self.environ.get("HTTP_X_BLOCK")
        if block_header:
            names = [b.strip() for b in block_header.split(",")]
            if len(names) == 1:
                return self.block(filepath, names[0], **context)
            parts = []
            for name in names:
                content = self.block(filepath, name, **context)
                parts.append(f'<template data-block="{name}">{content}</template>')
            return "".join(parts)

        content, tpl_root = self._resolve_template(filepath)
        return render_template_string(
            content, self._template_context(context), root_dir=tpl_root
        )

    def stream(self, filepath: str, **context: Any) -> Any:
        """Native HTML streaming response using generators."""
        # Detect block request
        block_header = self.environ.get("HTTP_X_BLOCK")
        if block_header:
            return self._stream_blocks(filepath, block_header, **context)

        from .templates import stream_template_string

        content, tpl_root = self._resolve_template(filepath)
        return stream_template_string(
            content, self._template_context(context), root_dir=tpl_root
        )

    def _stream_blocks(
        self, filepath: str, block_header: str, **context: Any
    ) -> Iterator[str]:
        """Internal helper to stream multiple template blocks.

        Wraps content in <template> tags ONLY for SPA block requests.
        For normal page loads, returns unwrapped content for better SEO and first paint.
        """
        names = [b.strip() for b in block_header.split(",")]
        content, tpl_root = self._resolve_template(filepath)

        # Check if this is a genuine SPA block request (has X-Block header)
        # vs a streaming response that happens to have multiple blocks
        is_spa_request = bool(self.environ.get("HTTP_X_BLOCK"))

        for name in names:
            self._current_block = name
            tpl_ctx = self._template_context(context)
            from .templates import render_block_string

            block_html = render_block_string(content, name, tpl_ctx, root_dir=tpl_root)

            # Only wrap in <template> for actual SPA block requests
            if is_spa_request:
                yield f'<template data-block="{name}">{block_html}</template>'
            else:
                # For normal page loads, return unwrapped content
                # This ensures better SEO, accessibility, and first paint
                yield block_html

    def block(
        self, filepath: str, block_name: Optional[str] = None, **context: Any
    ) -> str:
        """Render a specific block from an HTML template."""
        if block_name is None:
            block_name = self.environ.get("HTTP_X_BLOCK")
        if not block_name:
            raise ValueError(
                "block_name is required — pass it explicitly or use data-block on the form"
            )
        self._current_block = block_name
        content, tpl_root = self._resolve_template(filepath)
        return render_block_string(
            content, block_name, self._template_context(context), root_dir=tpl_root
        )

    def blocks(self, filepath: str, block_names: list[str], **context: Any) -> str:
        """Render multiple blocks intelligently.

        For SPA requests (with X-Block header): Returns wrapped in <template> tags
        For normal page loads: Returns unwrapped HTML for better SEO/accessibility

        Args:
            filepath: Template file path
            block_names: List of block names to render
            **context: Template context variables

        Returns:
            String containing rendered blocks (wrapped or unwrapped based on request type)

        Example:
            # In your page handler:
            def render(request):
                return request.blocks('page.html', ['main', 'title', 'sidebar'])

            # SPA request: Returns <template data-block="main">...</template>...
            # Normal request: Returns just the HTML content
        """
        is_spa = bool(self.environ.get("HTTP_X_BLOCK"))
        content, tpl_root = self._resolve_template(filepath)
        from .templates import render_block_string

        parts = []
        for name in block_names:
            self._current_block = name
            tpl_ctx = self._template_context(context)
            block_html = render_block_string(content, name, tpl_ctx, root_dir=tpl_root)

            if is_spa:
                parts.append(f'<template data-block="{name}">{block_html}</template>')
            else:
                parts.append(block_html)

        return "\n".join(parts)

    def static(self, filepath: str) -> str:
        """Return the public URL for a static asset, with versioning/optimization."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        root = self.environ.get("asok.root", os.getcwd())

        target_path = filepath
        # Smart WebP Swap
        if is_image(filepath) and not filepath.endswith(".webp"):
            # Try both: image.webp and image.jpg.webp
            webp_candidates = [
                filepath.rsplit(".", 1)[0] + ".webp",
                filepath + ".webp",
            ]
            for webp_path in webp_candidates:
                full_parts = os.path.join(root, "src/partials", webp_path.lstrip("/"))
                full_uploads = os.path.join(
                    root, "src/partials/uploads", webp_path.lstrip("/")
                )
                if os.path.isfile(full_parts) or os.path.isfile(full_uploads):
                    target_path = webp_path
                    break

        # Smart Min Swap (JS/CSS)
        elif app_ref and not app_ref.config.get("DEBUG"):
            if filepath.endswith(".js") and not filepath.endswith(".min.js"):
                min_path = filepath.rsplit(".", 1)[0] + ".min.js"
                # Check if min version exists in parts
                full_min = os.path.join(root, "src/partials", min_path.lstrip("/"))
                if os.path.isfile(full_min):
                    target_path = min_path
            elif (
                filepath.endswith(".css")
                and not filepath.endswith(".min.css")
                and not filepath.endswith(".build.css")
            ):
                min_path = filepath.rsplit(".", 1)[0] + ".min.css"
                full_min = os.path.join(root, "src/partials", min_path.lstrip("/"))
                if os.path.isfile(full_min):
                    target_path = min_path

        url = "/" + target_path.lstrip("/")
        if app_ref and not app_ref.config.get("DEBUG"):
            h = app_ref._static_hash(target_path)
            if h:
                url += f"?v={h}"
        elif app_ref and app_ref.config.get("DEBUG"):
            url += f"?v={int(time.time())}"
        return url

    def header(self, name: str, value: str) -> Request:
        """Add a custom response header.

        Args:
            name: Header name.
            value: Header value.

        Returns:
            The Request instance for chaining.
        """
        self.response_headers.append((name, value))
        return self

    def json(self, data: Any) -> str:
        """Return a JSON response."""
        self.content_type = "application/json"
        return json.dumps(data)

    def api(self, data: Any, status: int = 200) -> str:
        """Return a standardized JSON API success response."""
        self.status_code(status)
        self.content_type = "application/json"
        return json.dumps({"data": data, "status": status})

    def api_error(
        self, message: str, status: int = 400, errors: Optional[dict[str, Any]] = None
    ) -> str:
        """Return a standardized JSON API error response."""
        self.status_code(status)
        self.content_type = "application/json"
        payload: dict[str, Any] = {"error": message, "status": status}
        if errors is not None:
            payload["errors"] = errors
        return json.dumps(payload)

    def __(self, key: str, **kwargs: Any) -> str:
        """Translate a key using the current request language."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
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

    def redirect(self, url: str, safe: bool = True) -> None:
        """Abort current request and redirect to the given URL.

        If safe=True (default), redirects to external domains are blocked.
        """
        if safe and not is_safe_url(url):
            raise ValueError(
                f"Potentially unsafe redirect blocked: {url}. "
                "Use safe=False for external redirects."
            )
        raise RedirectException(url)

    def abort(self, code: int, message: Optional[str] = None) -> None:
        """Stop execution immediately and return an error response with the given status code."""
        raise AbortException(code, message)

    def not_found(self, message: Optional[str] = None) -> None:
        """Shortcut for abort(404)."""
        self.abort(404, message)

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

    def back(self, default: str = "/") -> None:
        """Abort current request and redirect back to the previous page (Referer).
        If no Referer is present, it redirects to the provided 'default' URL.
        """
        ref = self.environ.get("HTTP_REFERER", default)
        self.redirect(ref)

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

    def csrf_input(self) -> SafeString:
        """Return a hidden input field containing the CSRF token."""
        return SafeString(
            f'<input type="hidden" name="csrf_token" value="{self.csrf_token_value}" />'
        )

    def verify_csrf(self) -> None:
        """Verify the CSRF token in the request against the cookie/session token.

        Raises 403 Forbidden if the token is missing or invalid.
        """
        # Skip for safe methods
        if self.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
            return

        # 1. Strict Origin/Referer verification for HTTPS
        if self.scheme == "https":
            origin = self.headers.get("Origin") or self.headers.get("Referer")
            if not origin:
                self.abort(
                    403, "Strict CSRF: Origin or Referer header required for HTTPS."
                )

            try:
                from urllib.parse import urlparse

                parsed = urlparse(origin)
                # Compare netloc (host:port)
                if parsed.netloc != self.host:
                    self.abort(
                        403,
                        f"CSRF Origin mismatch: expected {self.host}, got {parsed.netloc}",
                    )
            except Exception:
                self.abort(403, "Invalid Origin or Referer format")

        # 2. Token verification
        token = self.form.get("csrf_token") or self.headers.get("X-CSRF-Token")

        if not token or not hmac.compare_digest(
            str(token), str(self.csrf_token_value or "")
        ):
            self.abort(403, "CSRF validation failed")

    @property
    def nonce(self) -> str:
        """Return a stable security nonce for the current request.

        The nonce is generated on first access and cached for the duration of the request.
        """
        if self._nonce is None:
            self._nonce = secrets.token_urlsafe(16)
        return self._nonce

    def _sign(self, value: Union[str, int]) -> str:
        """Sign a value using the application's secret key."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        if app_ref:
            return app_ref._sign(value)
        # Fallback (mostly for tests without full app env)
        key = self.environ.get("asok.secret_key", "").encode()
        if not key:
            raise RuntimeError("SECRET_KEY is not configured")
        return (
            f"{value}.{hmac.new(key, str(value).encode(), hashlib.sha256).hexdigest()}"
        )

    def _unsign(self, signed_value: Optional[str]) -> Optional[str]:
        """Verify the signature of a value and return the original if valid."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
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

    def _session_cookie(self, value: str, max_age: Optional[int] = None) -> str:
        """Formats the session cookie header."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        samesite = "Lax"
        if app_ref:
            samesite = app_ref.config.get("SESSION_SAMESITE", "Lax")

        cookie = f"asok_session={value}; HttpOnly; Path=/; SameSite={samesite}"
        if max_age is not None:
            cookie += f"; Max-Age={max_age}"
        if app_ref and not app_ref.config.get("DEBUG"):
            cookie += "; Secure"
        return cookie

    def login(self, user: Any, remember: bool = False) -> None:
        """Authenticate a user for the current session."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        if remember:
            max_age = 86400 * 365
        else:
            max_age = (
                app_ref.config.get("SESSION_MAX_AGE", 86400 * 30)
                if app_ref
                else 86400 * 30
            )
        signed_id = self._sign(user.id)
        self.environ["asok.session_cookie"] = self._session_cookie(signed_id, max_age)

        # Security: Rotate the server-side session ID to prevent session fixation
        self.session_regenerate()

        self._user_instance = user
        self._auth_resolved = True

    def has_role(self, *roles: str) -> bool:
        """Check if current user has any of the given roles."""
        u = self.user
        if not u:
            return False
        user_roles = getattr(u, "roles", None) or getattr(u, "role", None)
        if not user_roles:
            return False
        if isinstance(user_roles, str):
            user_roles = [r.strip() for r in user_roles.split(",")]
        return any(r in user_roles for r in roles)

    def require_role(self, *roles: str, redirect_url: str = "/") -> None:
        """Redirect if the user doesn't have the required roles."""
        if not self.has_role(*roles):
            raise RedirectException(redirect_url)

    def logout(self) -> None:
        """Clear user session and logout."""
        self.environ["asok.session_cookie"] = self._session_cookie("", 0)
        self._user_instance = None
        self._auth_resolved = True

    def authenticate(
        self, password_field: str = "password", **credentials: Any
    ) -> Optional[Any]:
        """Verify credentials and login user if successful."""
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        model_name = app_ref.config.get("AUTH_MODEL", "User") if app_ref else "User"
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            return None
        password = credentials.pop(password_field, None)
        if not password or not credentials:
            return None
        user = user_model.find(**credentials)
        if user and user.check_password(password_field, password):
            self.login(user)
            return user
        return None

    @property
    def is_authenticated(self) -> bool:
        """True if the current user is logged in."""
        return self.user is not None

    @property
    def auth(self) -> Any:
        """Access advanced auth helpers (Magic Links, OAuth, Tokens)."""

        class AuthProxy:
            magic = MagicLink
            oauth = OAuth
            token = BearerToken

        return AuthProxy

    @property
    def user(self) -> Optional[Any]:
        """Get the authenticated User instance for this request."""
        if self._auth_resolved:
            return self._user_instance

        app_ref: Optional[Asok] = self.environ.get("asok.app")
        model_name = app_ref.config.get("AUTH_MODEL", "User") if app_ref else "User"
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            self._auth_resolved = True
            return None

        user_id = None

        # 1. Try Authorization header (API Token)
        auth_header = self.environ.get("HTTP_AUTHORIZATION")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            user_id = BearerToken.verify(self, token)

        # 2. Try Session Cookie (Browser)
        if not user_id:
            user_id = self._unsign(self.cookies_dict.get("asok_session"))

        if user_id:
            try:
                self._user_instance = user_model.find(id=int(user_id))
            except Exception:
                pass

        self._auth_resolved = True
        return self._user_instance

    def send_file(
        self, filepath: str, filename: Optional[str] = None, as_attachment: bool = True
    ) -> str:
        """Return a file download response.

        Args:
            filepath:      Path to the file (absolute or relative to project root).
            filename:      Download filename (defaults to basename).
            as_attachment:  If True, browser downloads; if False, inline display.
        """
        root = self.environ.get("asok.root", os.getcwd())
        base = os.path.abspath(os.path.join(root, "src/partials/uploads"))
        if not os.path.isabs(filepath):
            filepath = os.path.join(base, filepath)
        filepath = os.path.abspath(filepath)

        # Block path traversal outside partials directory
        if not filepath.startswith(base + os.sep):
            self.status = "403 Forbidden"
            return "<h1>403 Forbidden</h1>"

        if not os.path.isfile(filepath):
            self.status = "404 Not Found"
            return "<h1>404 Not Found</h1>"

        fname = filename or os.path.basename(filepath)
        mimetype, _ = mimetypes.guess_type(filepath)
        self.content_type = mimetype or "application/octet-stream"

        disposition = "attachment" if as_attachment else "inline"
        file_size = os.path.getsize(filepath)
        self.environ.setdefault("asok.extra_headers", []).extend(
            [
                ("Content-Disposition", f'{disposition}; filename="{fname}"'),
                ("Content-Length", str(file_size)),
            ]
        )

        # Stream large files (> 1 MB) to avoid loading them entirely into memory
        if file_size > 1 * 1024 * 1024:
            self.environ["asok.stream_file"] = filepath
        else:
            with open(filepath, "rb") as f:
                self.environ["asok.binary_response"] = f.read()

        return ""

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
    def location(self) -> dict[str, Any]:
        """Get the geographic location of the client based on their IP address.

        Requires a local database at .asok/geo.csv. If missing, returns 'Unknown' values.
        Returns a dictionary with 'city', 'country', 'lat', and 'lon'.
        """
        cache = self.__dict__.setdefault("_location_cache", None)
        if cache:
            return cache
        loc = IPLocation.get_instance().lookup(self.ip)
        self.__dict__["_location_cache"] = loc
        return loc

    def require_auth(self, redirect_url: str = "/login") -> None:
        """Ensure the user is authenticated, else redirect to login with a 'next' parameter."""
        if not self.is_authenticated:
            next_url = self.path
            if self.query_string:
                next_url += "?" + self.query_string
            separator = "&" if "?" in redirect_url else "?"
            raise RedirectException(f"{redirect_url}{separator}next={next_url}")

    @property
    def session(self) -> Session:
        """Access the current request's session."""
        if self._session is not None:
            return self._session
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "_session_store"):
            self._session = Session()
            return self._session
        store = app_ref._session_store
        signed_sid = self.cookies_dict.get("asok_sid")
        sid = self._unsign(signed_sid) if signed_sid else None
        data = store.load(sid) if sid else None
        if data is not None:
            self._session = Session(data)
            self._session.sid = sid
        else:
            self._session = Session()
            self._session.sid = store.generate_sid()
        self._session.modified = False
        return self._session

    def session_regenerate(self) -> None:
        """Rotate the session ID while preserving all existing data.

        Crucial for preventing session fixation attacks after successful login.
        """
        app_ref: Optional[Asok] = self.environ.get("asok.app")
        if not app_ref or not hasattr(app_ref, "_session_store"):
            return

        store = app_ref._session_store
        sess = self.session  # Ensure session is loaded/created
        if sess.sid:
            new_sid = store.regenerate(sess.sid)
            sess.sid = new_sid
            sess.modified = True
        self.modified = True

    @property
    def scheme(self) -> str:
        """The URL scheme (http or https)."""
        return self.environ.get("wsgi.url_scheme", "http")

    @property
    def host(self) -> str:
        """The Host header value."""
        return self.environ.get("HTTP_HOST", "localhost")

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
