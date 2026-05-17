from __future__ import annotations

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
    ForbiddenError,
    NotFoundError,
    RedirectException,
    UnauthorizedError,
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

    # Magic bytes pour validation MIME
    # Support des formats les plus courants : images, audio, vidéo, documents
    # Note: RIFF et ftyp sont gérés spécialement dans validate_mime_type() car ambigus
    _MAGIC_BYTES = {
        # ── Images ──────────────────────────────────────
        b"\xff\xd8\xff": ("image/jpeg", [".jpg", ".jpeg"]),
        b"\x89PNG\r\n\x1a\n": ("image/png", [".png"]),
        b"GIF87a": ("image/gif", [".gif"]),
        b"GIF89a": ("image/gif", [".gif"]),
        b"RIFF": ("image/webp", [".webp", ".wav", ".avi"]),
        b"BM": ("image/bmp", [".bmp"]),
        b"II*\x00": ("image/tiff", [".tif", ".tiff"]),  # TIFF (little-endian)
        b"MM\x00*": ("image/tiff", [".tif", ".tiff"]),  # TIFF (big-endian)
        b"\x00\x00\x01\x00": ("image/x-icon", [".ico"]),
        b"<?xml": ("image/svg+xml", [".svg"]),  # SVG (XML)
        b"<svg": ("image/svg+xml", [".svg"]),  # SVG direct
        # ── Audio ───────────────────────────────────────
        b"ID3": ("audio/mpeg", [".mp3"]),  # MP3 avec ID3v2
        b"\xff\xfb": ("audio/mpeg", [".mp3"]),  # MP3 sans tag
        b"\xff\xf3": ("audio/mpeg", [".mp3"]),  # MP3 MPEG-2.5
        b"\xff\xf2": ("audio/mpeg", [".mp3"]),  # MP3 MPEG-2
        b"fLaC": ("audio/flac", [".flac"]),  # FLAC
        b"OggS": ("audio/ogg", [".ogg", ".oga"]),  # OGG Vorbis/Opus
        b"\xff\xf1": ("audio/aac", [".aac"]),  # AAC (ADTS)
        b"\xff\xf9": ("audio/aac", [".aac"]),  # AAC
        # ── Vidéo ───────────────────────────────────────
        b"ftyp": (
            "video/mp4",
            [".mp4", ".m4a", ".mov", ".3gp"],
        ),  # Ambigu - traité spécialement
        b"\x1aE\xdf\xa3": (
            "video/webm",
            [".webm", ".mkv"],
        ),  # WebM/Matroska (Matroska header)
        # ── Documents ───────────────────────────────────
        b"%PDF": ("application/pdf", [".pdf"]),
        b"PK\x03\x04": (
            "application/zip",
            [".zip", ".docx", ".xlsx", ".pptx", ".jar", ".apk", ".odt", ".ods"],
        ),
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1": (
            "application/msword",
            [".doc", ".xls", ".ppt"],
        ),  # MS Office legacy
        b"{\\rtf": ("text/rtf", [".rtf"]),  # Rich Text Format
        # ── Archives ────────────────────────────────────
        b"\x1f\x8b": ("application/gzip", [".gz", ".gzip"]),
        b"BZh": ("application/x-bzip2", [".bz2"]),
        b"Rar!\x1a\x07": ("application/x-rar-compressed", [".rar"]),
        b"7z\xbc\xaf\x27\x1c": ("application/x-7z-compressed", [".7z"]),
    }

    def __init__(self, filename: str, content: bytes):
        self.filename: str = _sanitize_filename(filename)
        self.content: bytes = content
        self.size: int = len(content)
        self._validated: bool = False

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

    def validate_mime_type(self, allowed_types: Optional[list[str]] = None) -> bool:
        """Validate the file MIME type using magic bytes.

        Args:
            allowed_types: List of allowed MIME types (e.g., ['image/jpeg', 'image/png'])
                          If None, validates against all known types

        Returns:
            True if valid, False otherwise

        Raises:
            ValueError: If file type is not allowed or unknown
        """
        if not self.content:
            raise ValueError("Cannot validate empty file")

        # Vérifier les magic bytes avec gestion des formats ambigus
        detected_mime = None
        detected_exts = []

        # RIFF est ambigu (WebP, WAV, AVI) - vérifier la sous-signature
        if self.content.startswith(b"RIFF") and len(self.content) >= 12:
            riff_type = self.content[8:12]
            if riff_type == b"WEBP":
                detected_mime = "image/webp"
                detected_exts = [".webp"]
            elif riff_type == b"WAVE":
                detected_mime = "audio/wav"
                detected_exts = [".wav"]
            elif riff_type == b"AVI ":
                detected_mime = "video/avi"
                detected_exts = [".avi"]

        # ftyp est ambigu (MP4, MOV, M4A, 3GP) - vérifier le type
        elif self.content.startswith(b"ftyp") or (
            len(self.content) >= 8 and self.content[4:8] == b"ftyp"
        ):
            # Extraire le type ftyp (4 octets après "ftyp")
            ftyp_start = self.content.find(b"ftyp")
            if ftyp_start != -1 and len(self.content) >= ftyp_start + 8:
                ftyp_brand = self.content[ftyp_start + 4 : ftyp_start + 8]
                if ftyp_brand.startswith(b"M4A"):
                    detected_mime = "audio/mp4"
                    detected_exts = [".m4a"]
                elif ftyp_brand.startswith(b"3gp"):
                    detected_mime = "video/3gpp"
                    detected_exts = [".3gp"]
                elif ftyp_brand in (b"isom", b"mp41", b"mp42"):
                    detected_mime = "video/mp4"
                    detected_exts = [".mp4"]
                elif ftyp_brand.startswith(b"qt  "):
                    detected_mime = "video/quicktime"
                    detected_exts = [".mov"]

        # Sinon, vérifier normalement
        if not detected_mime:
            for magic, (mime, exts) in self._MAGIC_BYTES.items():
                if self.content.startswith(magic):
                    # Ignorer RIFF et ftyp car déjà traités
                    if magic in (b"RIFF", b"ftyp"):
                        continue
                    detected_mime = mime
                    detected_exts = exts
                    break

        if not detected_mime:
            raise ValueError(
                f"Unknown or unsupported file type. "
                f"Supported types: {', '.join(set(m for m, _ in self._MAGIC_BYTES.values()))}"
            )

        # Vérifier contre la liste autorisée
        if allowed_types and detected_mime not in allowed_types:
            raise ValueError(
                f"File type '{detected_mime}' not allowed. "
                f"Allowed types: {', '.join(allowed_types)}"
            )

        # Vérifier que l'extension correspond au type détecté
        import os

        _, ext = os.path.splitext(self.filename.lower())
        if ext not in detected_exts:
            raise ValueError(
                f"File extension '{ext}' does not match detected type '{detected_mime}'. "
                f"Expected one of: {', '.join(detected_exts)}"
            )

        self._validated = True
        return True

    def save(
        self,
        destination: str,
        validate: bool = True,
        allowed_types: Optional[list[str]] = None,
        secure_filename: bool = True,
    ) -> str:
        """Save the uploaded file to disk.

        Args:
            destination: Path relative to project root or absolute path.
            validate: If True, validate MIME type before saving (default: True)
            allowed_types: List of allowed MIME types for validation.
                          SECURITY WARNING: Should always be specified! Allowing all
                          types can lead to security vulnerabilities.
            secure_filename: If True, rename file with UUID for security (default: True)

        Returns:
            The absolute path where the file was saved.

        Raises:
            ValueError: If validation fails or path traversal is detected

        SECURITY: Always specify allowed_types to prevent malicious file uploads.
        Example: file.save('uploads/', allowed_types=['image/jpeg', 'image/png'])
        """
        import logging

        # SECURITY WARNING: Log if allowed_types not specified
        if validate and allowed_types is None:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: File upload without allowed_types restriction. "
                "This allows any file type with valid magic bytes. "
                "Always specify allowed_types=['image/jpeg', 'image/png', ...] "
                "for secure file uploads."
            )

        # Validation MIME type AVANT d'écrire sur disque
        if not validate:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: File validation disabled (validate=False). "
                "This is dangerous and should only be used for trusted sources."
            )
        elif not self._validated:
            self.validate_mime_type(allowed_types)

        # Detect if the user wants to save into a directory
        is_dir = destination.endswith(("/", "\\"))

        # Base directory
        root = os.getcwd()
        base_dir = os.path.abspath(os.path.join(root, "src/partials/uploads"))

        # Resolve full path
        if not os.path.isabs(destination):
            dest = os.path.abspath(os.path.join(base_dir, destination))
        else:
            dest = os.path.abspath(destination)

        # SECURITY: Generate secure filename if requested
        if secure_filename:
            import uuid

            _, ext = os.path.splitext(self.filename)
            safe_name = f"{uuid.uuid4()}{ext.lower()}"
        else:
            # Use original filename sanitized
            from .utils.security import secure_filename as sanitize_filename

            safe_name = sanitize_filename(self.filename)

        # If it's a directory or already exists as one, append filename
        if is_dir or os.path.isdir(dest):
            os.makedirs(dest, exist_ok=True)
            dest = os.path.join(dest, safe_name)
        else:
            # Replace filename in destination with safe name
            dest_dir = os.path.dirname(dest)
            dest = os.path.join(dest_dir, safe_name)

        print(f"  ➜ [ASOK] Saving file to: {dest}")

        # Security check
        try:
            common = os.path.commonpath([dest, base_dir])
            if common != base_dir:
                raise ValueError(f"Path traversal blocked: {destination}")
        except Exception:
            raise ValueError(f"Invalid destination: {destination}")

        # Ensure parent directory exists
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if os.path.exists(dest):
            base, ext = os.path.splitext(dest)
            counter = 1
            while os.path.exists(f"{base}_{counter}{ext}"):
                counter += 1
            dest = f"{base}_{counter}{ext}"

        with open(dest, "wb") as f:
            f.write(self.content)

        # SECURITY: Set restrictive permissions (read-only for owner and group)
        # 0o644 = rw-r--r-- (owner can read/write, others can only read)
        os.chmod(dest, 0o644)

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

        self.cookies_dict = {}
        cookie_header = environ.get("HTTP_COOKIE", "")
        if cookie_header:
            for pair in cookie_header.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    val = v.strip()
                    # Handle optional quotes
                    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
                        val = val[1:-1]
                    self.cookies_dict[k.strip()] = val

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
                # Extract boundary
                import re

                boundary_match = re.search(r"boundary=([^;]+)", enc_content_type)
                if boundary_match:
                    boundary = boundary_match.group(1).strip().encode()
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

        # Automatic extension resolution if file not found
        if not os.path.isfile(path):
            # 1. Try appending extensions (for request.html('page'))
            for ext in (".html", ".asok"):
                if os.path.isfile(path + ext):
                    path = path + ext
                    break

            # 2. Try swapping extensions if still not found (for request.html('page.html') -> page.asok)
            if not os.path.isfile(path):
                base_path, current_ext = os.path.splitext(path)
                if current_ext == ".html" and os.path.isfile(base_path + ".asok"):
                    path = base_path + ".asok"
                elif current_ext == ".asok" and os.path.isfile(base_path + ".html"):
                    path = base_path + ".html"

        # CONVENTION: Enforce strict naming convention for page templates
        # Only allow 'page.html' or 'page.asok' as template names
        # Exception: partials (src/partials/), layouts (src/html/), and components can have any name
        normalized_path = path.replace("\\", "/")
        is_partial = (
            "/partials/" in normalized_path
            or "/html/" in normalized_path
            or "/components/" in normalized_path
        )

        if not is_partial:
            basename = os.path.basename(path)
            if basename not in ("page.html", "page.asok"):
                raise ValueError(
                    f"Invalid template name: '{basename}'. "
                    f"Page templates must be named 'page.html' or 'page.asok'. "
                    f"This convention ensures code readability and consistency.\n"
                    f"  ✓ Valid: src/pages/contact/page.html\n"
                    f"  ✗ Invalid: src/pages/contact/contact_form.html\n"
                    f"Note: Partials (src/partials/), layouts (src/html/), and components can have any name."
                )

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
                app_ref.config.get("SECRET_KEY") if app_ref else os.getenv("SECRET_KEY")
            )
            if not secret:
                raise RuntimeError(
                    "SECRET_KEY is not configured. This should never happen if Asok() is properly initialized."
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
            for name in names:
                if name.startswith("#"):
                    raise ValueError(
                        f"Invalid block name '{name}'. Block names should not include the '#' prefix. "
                        f"Check your 'data-block' attributes in templates."
                    )
            if len(names) == 1:
                return self.block(filepath, names[0], **context)
            parts = []
            for name in names:
                content = self.block(filepath, name, **context)
                parts.append(f'<template data-block="{name}">{content}</template>')
            return "".join(parts)

        content, tpl_root = self._resolve_template(filepath)
        if not hasattr(self, "_asok_templates"):
            self._asok_templates = []
        self._asok_templates.append(filepath)
        return render_template_string(
            content,
            self._template_context(context),
            root_dir=tpl_root,
            inject_block_markers=True,  # Inject markers for data-block targeting
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
            content,
            self._template_context(context),
            root_dir=tpl_root,
            inject_block_markers=True,  # Inject markers for data-block targeting
        )

    def _stream_blocks(
        self, filepath: str, block_header: str, **context: Any
    ) -> Iterator[str]:
        """Internal helper to stream multiple template blocks.

        Wraps content in <template> tags ONLY for SPA block requests.
        For normal page loads, returns unwrapped content for better SEO and first paint.
        """
        names = [b.strip() for b in block_header.split(",")]
        for name in names:
            if not name:
                continue
            if name.startswith("#"):
                raise ValueError(
                    f"Invalid block name '{name}'. Use the block name directly without the '#' prefix."
                )
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

        # CRITICAL: Include scoped CSS/JS in SPA block responses
        # This ensures that page-specific styles and scripts are preserved during navigation
        if is_spa_request and hasattr(self, "scoped_assets"):
            from .utils.css import scope_css
            from .utils.minify import minify_css, minify_js

            page_id = getattr(self, "page_id", None)

            # Include scoped CSS if it exists
            if self.scoped_assets.get("css") and page_id:
                try:
                    with open(self.scoped_assets["css"], "r", encoding="utf-8") as f:
                        raw_css = f.read()
                    scoped_css = scope_css(raw_css, page_id)
                    # Minify in production
                    if not self.environ.get("DEBUG"):
                        scoped_css = minify_css(scoped_css)
                    # SECURITY: Escape page_id for safe HTML attribute injection
                    # Prevent CSS from breaking </style> tag by replacing it
                    import html

                    safe_page_id = html.escape(page_id, quote=True)
                    safe_css = scoped_css.replace("</style>", "<\\/style>")
                    yield f'<style id="asok-scoped-css" data-page-id="{safe_page_id}">{safe_css}</style>'
                except Exception:
                    pass  # Silently fail if CSS can't be loaded

            # Include scoped JS if it exists
            if self.scoped_assets.get("js"):
                try:
                    with open(self.scoped_assets["js"], "r", encoding="utf-8") as f:
                        raw_js = f.read()
                    from .utils.js import scope_js

                    scoped_js = scope_js(raw_js, page_id) if page_id else raw_js
                    # Minify in production
                    if not self.environ.get("DEBUG"):
                        scoped_js = minify_js(scoped_js)
                    nonce = getattr(self, "nonce", "")
                    # SECURITY: Prevent JS from breaking </script> tag
                    # Replace </script> with <\/script> to avoid premature tag closure
                    safe_js = scoped_js.replace("</script>", "<\\/script>")
                    yield f"<script id=\"asok-scoped-js\" nonce=\"{nonce}\">(function(){{const init=function(){{{safe_js}}};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();}})();</script>"
                except Exception:
                    pass  # Silently fail if JS can't be loaded

    def block(
        self, filepath: str, block_name: Optional[str] = None, **context: Any
    ) -> str:
        """Render a specific block from an HTML template.

        Returns the block content for data-block updates. The JavaScript will use
        HTML comment markers to locate and replace the content.
        """
        if block_name is None:
            block_name = self.environ.get("HTTP_X_BLOCK")
        if not block_name:
            raise ValueError(
                "block_name is required — pass it explicitly or use data-block on the form"
            )
        # Enforce strict block naming (no # prefix)
        if block_name.startswith("#"):
            raise ValueError(
                f"Invalid block name '{block_name}'. Block names should not include the '#' prefix. "
                f"Use '{block_name.lstrip('#')}' instead."
            )
        self._current_block = block_name
        if not hasattr(self, "_asok_blocks"):
            self._asok_blocks = []
        self._asok_blocks.append(block_name)
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
        if safe and not is_safe_url(url, allowed_host=self.host):
            raise ValueError(
                f"Potentially unsafe redirect blocked: {url}. "
                "Use safe=False for external redirects."
            )
        from .exceptions import RedirectException

        raise RedirectException(url)

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

    def back(self, default: str = "/") -> None:
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

    def _is_safe_redirect(self, url: str) -> bool:
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
            from urllib.parse import urlparse

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

    def verify_csrf(self):
        """Verify the CSRF token from headers, form data, or JSON body.

        Raises SecurityError if validation fails.
        """
        if self._csrf_verified:
            return

        from .exceptions import SecurityError

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
                from urllib.parse import urlparse

                parsed = urlparse(origin)
                # Compare netloc (host:port)
                if parsed.netloc != self.host:
                    raise SecurityError(
                        f"CSRF Origin mismatch: expected {self.host}, got {parsed.netloc}",
                    )
            except Exception:
                raise SecurityError("Invalid Origin or Referer format")

        # 2. Token verification
        # DEBUG
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
        import secrets

        new_token = secrets.token_hex(32)
        self.csrf_token_value = new_token
        # Le nouveau token sera automatiquement envoyé via Set-Cookie et X-CSRF-Token header

    @property
    def nonce(self) -> str:
        """Return a stable security nonce for the current request.

        The nonce is generated on first access and cached for the duration of the request.
        """
        val = getattr(self, "_nonce", None)
        if not val or not isinstance(val, str) or len(val) < 10:
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

        # SECURITY: Rotate the server-side session ID to prevent session fixation
        self.session_regenerate()

        # SECURITY: Rotate CSRF token to prevent CSRF token fixation
        import secrets

        self.csrf_token_value = secrets.token_hex(32)

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
        """Clear user session and logout.

        SECURITY: Rotates CSRF token after logout.
        """
        self.environ["asok.session_cookie"] = self._session_cookie("", 0)

        # Clear server-side session as well
        try:
            self.session.clear()
        except Exception:
            pass

        # SECURITY: Rotate CSRF token after logout
        import secrets

        self.csrf_token_value = secrets.token_hex(32)

        self._user_instance = None
        self._auth_resolved = False

    def authenticate(
        self, password_field: str = "password", **credentials: Any
    ) -> Optional[Any]:
        """Verify credentials and login user if successful.

        SECURITY: Implements rate limiting to prevent brute force attacks.
        Maximum 5 failed attempts per IP address within 15 minutes.
        """
        import logging
        import time

        from .cache import default_cache

        app_ref: Optional[Asok] = self.environ.get("asok.app")
        model_name = app_ref.config.get("AUTH_MODEL", "User") if app_ref else "User"
        user_model = MODELS_REGISTRY.get(model_name)
        if not user_model:
            return None

        password = credentials.pop(password_field, None)
        if not password or not credentials:
            return None

        # SECURITY: Rate limiting by IP address
        rate_limit_key = f"auth_attempts:{self.ip}"
        attempts = default_cache.get(rate_limit_key, 0)

        # Block if too many failed attempts
        MAX_ATTEMPTS = 5
        LOCKOUT_DURATION = 900  # 15 minutes in seconds

        if attempts >= MAX_ATTEMPTS:
            logging.getLogger(__name__).warning(
                "SECURITY: Authentication blocked for IP %s: too many failed attempts (%d)",
                self.ip,
                attempts,
            )
            # Slow down attacker
            time.sleep(2)
            return None

        user = user_model.find(**credentials)
        if user and user.check_password(password_field, password):
            # SECURITY: Reset counter on successful login
            default_cache.forget(rate_limit_key)
            self.login(user)
            return user

        # SECURITY: Increment failed attempts counter
        default_cache.set(rate_limit_key, attempts + 1, ttl=LOCKOUT_DURATION)

        # Slow down failed attempts to make brute force impractical
        time.sleep(1)

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

        # 2. Try Session (Browser)
        if not user_id:
            try:
                # Prioritize server-side session value (crucial for impersonation)
                user_id = self.session.get("user_id")
            except Exception:
                user_id = None

            if not user_id:
                # Fallback to signed cookie
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

        SECURITY: Enhanced path traversal protection using pathlib.
        """
        from pathlib import Path

        root = Path(self.environ.get("asok.root", os.getcwd()))
        base_dir = (root / "src/partials/uploads").resolve()

        # SECURITY: Take ONLY the filename (basename) to prevent any path traversal
        # This ensures even "../" or "../../" in the input cannot escape the directory
        clean_name = Path(filepath).name
        if not clean_name or clean_name in (".", ".."):
            self.status = "403 Forbidden"
            return "<h1>403 Forbidden</h1>"

        # Resolve the full path
        try:
            full_path = (base_dir / clean_name).resolve(strict=True)
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
        from .utils.security import secure_filename

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
        from .utils.geo import Countries

        country_info = Countries.get(loc.get("country", "")) or {
            "iso": "Unknown",
            "name": "Unknown",
            "dial_code": "",
            "flag": "🌐",
            "capital": "Unknown",
            "continent": "Unknown",
            "currency": "Unknown",
            "languages": "Unknown",
        }

        geo_data = {
            "ip": self.ip,
            "city": loc.get("city", "Unknown"),
            "country": loc.get("country", "Unknown"),
            "lat": loc.get("lat", 0.0),
            "lon": loc.get("lon", 0.0),
            **country_info,
        }

        # 3. Add derived info
        from .utils.geo import Countries as GeoUtils

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
