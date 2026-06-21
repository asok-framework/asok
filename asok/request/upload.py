from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional, Union

from asok.utils.image import is_image, optimize_image

_RE_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

_DANGEROUS_EXTS = {
    "php", "phtml", "php3", "php4", "php5", "phps", "pht",
    "exe", "com", "bat", "cmd", "sh", "bash", "zsh", "csh",
    "pl", "py", "rb", "js", "jsp", "asp", "aspx", "cgi",
    "dll", "so", "dylib",
}


def _sanitize_filename(filename: str) -> str:
    """Sanitize an uploaded filename to prevent path traversal and unsafe characters.

    SECURITY: Detects and blocks double extensions (e.g., shell.php.png) to prevent
    bypassing file type restrictions.
    """
    # Strip any path components (Windows or Unix)
    filename = filename.replace("\\", "/")
    filename = filename.split("/")[-1]
    # Remove unsafe characters
    filename = _RE_UNSAFE_FILENAME.sub("_", filename)
    # Strip leading dots/spaces to prevent hidden files
    filename = filename.lstrip(". ")

    _check_double_extensions(filename)

    # Fallback for empty names
    if not filename:
        filename = "upload"
    return filename


def _check_double_extensions(filename: str) -> None:
    """Raise ValueError if the filename contains dangerous intermediate extensions."""
    parts = filename.split(".")
    if len(parts) > 2:
        for ext in parts[1:-1]:
            if ext.lower() in _DANGEROUS_EXTS:
                raise ValueError(
                    f"Double extension detected: '{filename}'. "
                    f"Files with dangerous intermediate extensions (e.g., .php, .exe) are not allowed."
                )


class UploadedFile:
    """Wrapper for a file uploaded via multipart/form-data."""

    # Magic bytes for MIME validation
    # Support for the most common formats: images, audio, video, documents
    # Note: RIFF and ftyp are handled specially in validate_mime_type() because they are ambiguous
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
        b"ID3": ("audio/mpeg", [".mp3"]),  # MP3 with ID3v2
        b"\xff\xfb": ("audio/mpeg", [".mp3"]),  # MP3 without tag
        b"\xff\xf3": ("audio/mpeg", [".mp3"]),  # MP3 MPEG-2.5
        b"\xff\xf2": ("audio/mpeg", [".mp3"]),  # MP3 MPEG-2
        b"fLaC": ("audio/flac", [".flac"]),  # FLAC
        b"OggS": ("audio/ogg", [".ogg", ".oga"]),  # OGG Vorbis/Opus
        b"\xff\xf1": ("audio/aac", [".aac"]),  # AAC (ADTS)
        b"\xff\xf9": ("audio/aac", [".aac"]),  # AAC
        # ── Video ───────────────────────────────────────
        b"ftyp": (
            "video/mp4",
            [".mp4", ".m4a", ".mov", ".3gp"],
        ),  # Ambiguous - handled specially
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

    def __init__(
        self, filename: str, content: bytes, content_type: Optional[str] = None
    ):
        self.filename: str = _sanitize_filename(filename)
        self.content: bytes = content
        self.size: int = len(content)
        self.content_type: Optional[str] = content_type
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

    def _lookup_riff_type(self, riff_type: bytes) -> tuple[str, list] | tuple[None, None]:
        if riff_type == b"WEBP":
            return "image/webp", [".webp"]
        if riff_type == b"WAVE":
            return "audio/wav", [".wav"]
        if riff_type == b"AVI ":
            return "video/avi", [".avi"]
        return None, None

    def _detect_riff_mime(self) -> tuple[str, list] | tuple[None, None]:
        """Detect MIME type for RIFF-based formats (WebP, WAV, AVI)."""
        if not self.content.startswith(b"RIFF") or len(self.content) < 12:
            return None, None
        return self._lookup_riff_type(self.content[8:12])

    def _has_ftyp_signature(self) -> bool:
        if self.content.startswith(b"ftyp"):
            return True
        return len(self.content) >= 8 and self.content[4:8] == b"ftyp"

    def _get_ftyp_brand(self) -> Optional[bytes]:
        if not self._has_ftyp_signature():
            return None
        ftyp_start = self.content.find(b"ftyp")
        if ftyp_start == -1 or len(self.content) < ftyp_start + 8:
            return None
        return self.content[ftyp_start + 4: ftyp_start + 8]

    def _lookup_ftyp_brand(self, ftyp_brand: bytes) -> tuple[str, list] | tuple[None, None]:
        if ftyp_brand.startswith(b"M4A"):
            return "audio/mp4", [".m4a"]
        if ftyp_brand.startswith(b"3gp"):
            return "video/3gpp", [".3gp"]
        if ftyp_brand in (b"isom", b"mp41", b"mp42"):
            return "video/mp4", [".mp4"]
        if ftyp_brand.startswith(b"qt  "):
            return "video/quicktime", [".mov"]
        return None, None

    def _detect_ftyp_mime(self) -> tuple[str, list] | tuple[None, None]:
        """Detect MIME type for ftyp-based formats (MP4, MOV, M4A, 3GP)."""
        brand = self._get_ftyp_brand()
        if brand is None:
            return None, None
        return self._lookup_ftyp_brand(brand)

    def _detect_generic_mime(self) -> tuple[str, list] | tuple[None, None]:
        """Detect MIME type via the generic magic bytes table."""
        for magic, (mime, exts) in self._MAGIC_BYTES.items():
            if self.content.startswith(magic):
                return mime, exts
        return None, None

    def _detect_mime(self) -> tuple[str, list]:
        """Detect MIME type from content, returning (mime, extensions)."""
        mime, exts = self._detect_riff_mime()
        if mime:
            return mime, exts
        mime, exts = self._detect_ftyp_mime()
        if mime:
            return mime, exts
        mime, exts = self._detect_generic_mime()
        if mime:
            return mime, exts
        all_mimes = ", ".join({m for m, _ in self._MAGIC_BYTES.values()})
        raise ValueError(f"Unknown or unsupported file type. Supported types: {all_mimes}")

    def _check_allowed_types(self, detected_mime: str, allowed_types: Optional[list[str]]) -> None:
        """Raise ValueError if detected_mime is not in allowed_types."""
        if allowed_types and detected_mime not in allowed_types:
            raise ValueError(
                f"File type '{detected_mime}' not allowed. "
                f"Allowed types: {', '.join(allowed_types)}"
            )

    def _check_extension_match(self, detected_mime: str, detected_exts: list) -> None:
        """Raise ValueError if the file extension doesn't match the detected MIME type."""
        _, ext = os.path.splitext(self.filename.lower())
        if ext not in detected_exts:
            raise ValueError(
                f"File extension '{ext}' does not match detected type '{detected_mime}'. "
                f"Expected one of: {', '.join(detected_exts)}"
            )

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

        detected_mime, detected_exts = self._detect_mime()
        self._check_allowed_types(detected_mime, allowed_types)
        self._check_extension_match(detected_mime, detected_exts)

        self._validated = True
        return True

    # ── save() helpers ──────────────────────────────────────────────────────

    def _warn_no_allowed_types(self) -> None:
        """Log a security warning when allowed_types is not specified."""
        logging.getLogger(__name__).warning(
            "SECURITY WARNING: File upload without allowed_types restriction. "
            "This allows any file type with valid magic bytes. "
            "Always specify allowed_types=['image/jpeg', 'image/png', ...] "
            "for secure file uploads."
        )

    def _warn_validation_disabled(self) -> None:
        """Log a security warning when validation is disabled."""
        logging.getLogger(__name__).warning(
            "SECURITY WARNING: File validation disabled (validate=False). "
            "This is dangerous and should only be used for trusted sources."
        )

    def _build_safe_name(self, secure_filename: bool) -> str:
        """Generate a safe filename (UUID-based or sanitized original)."""
        if secure_filename:
            import uuid
            _, ext = os.path.splitext(self.filename)
            return f"{uuid.uuid4()}{ext.lower()}"
        from asok.utils.security import secure_filename as sanitize_filename
        return sanitize_filename(self.filename)

    def _is_svg_file(self, safe_name: str) -> bool:
        _, ext = os.path.splitext(safe_name)
        if ext.lower() == ".svg":
            return True
        try:
            detected_mime, _ = self._detect_mime()
            return detected_mime == "image/svg+xml"
        except Exception:
            return False

    def _sanitize_svg_content(self, content: bytes, safe_name: str, validate: bool, logger) -> bytes:
        """Sanitize SVG content if the file is an SVG and validation is enabled."""
        if not validate:
            return content

        if self._is_svg_file(safe_name):
            from asok.utils.svg_sanitizer import sanitize_svg
            try:
                sanitized = sanitize_svg(content)
                logger.debug("SVG file sanitized: %s", safe_name)
                return sanitized
            except ValueError as e:
                raise ValueError(f"SVG sanitization failed: {e}")
        return content

    def _save_to_s3(self, destination: str, secure_filename: bool, private: bool, validate: bool) -> str:
        """Upload file to S3 storage and return the URL."""
        from asok.core.storage import get_storage
        logger = logging.getLogger(__name__)

        safe_name = self._build_safe_name(secure_filename)
        is_dir = destination.endswith(("/", "\\"))
        if is_dir:
            upload_to = destination.strip("/\\")
        else:
            upload_to = os.path.dirname(destination).strip("/\\")

        content_to_upload = self._sanitize_svg_content(self.content, safe_name, validate, logger)
        url = get_storage().save(safe_name, content_to_upload, upload_to, private=private)
        self.filename = safe_name
        return url

    def _build_dest_path(self, base_dir: str, destination: str, safe_name: str) -> str:
        if not os.path.isabs(destination):
            dest = os.path.abspath(os.path.join(base_dir, destination))
        else:
            dest = os.path.abspath(destination)

        is_dir = destination.endswith(("/", "\\"))
        if is_dir or os.path.isdir(dest):
            os.makedirs(dest, exist_ok=True)
            return os.path.join(dest, safe_name)
        return os.path.join(os.path.dirname(dest), safe_name)

    def _validate_local_dest_safety(self, dest: str, base_dir: str, destination: str) -> None:
        try:
            resolved_dest = os.path.realpath(dest)
            resolved_base = os.path.realpath(base_dir)
            common = os.path.commonpath([resolved_dest, resolved_base])
            if common != resolved_base:
                raise ValueError(f"Path traversal blocked: {destination}")
        except ValueError:
            raise
        except Exception:
            raise ValueError(f"Invalid destination: {destination}")

    def _resolve_local_dest(self, destination: str, safe_name: str) -> str:
        """Resolve and validate the local filesystem destination path."""
        root = os.getcwd()
        base_dir = os.path.abspath(os.path.join(root, "src/partials/uploads"))
        dest = self._build_dest_path(base_dir, destination, safe_name)
        self._validate_local_dest_safety(dest, base_dir, destination)
        return dest

    def _deduplicate_dest(self, dest: str) -> str:
        """Append a counter suffix if the destination file already exists."""
        if not os.path.exists(dest):
            return dest
        base, ext = os.path.splitext(dest)
        counter = 1
        while os.path.exists(f"{base}_{counter}{ext}"):
            counter += 1
        return f"{base}_{counter}{ext}"

    def _write_local_file(self, dest: str, content: bytes, private: bool) -> None:
        """Write content to disk and apply file permissions."""
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(content)
        chmod = 0o600 if private else 0o644
        os.chmod(dest, chmod)

    def _maybe_optimize_image(self, dest: str) -> None:
        """Run image optimization if enabled and applicable."""
        if os.environ.get("IMAGE_OPTIMIZATION") == "true" and is_image(dest):
            keep = os.environ.get("IMAGE_KEEP_ORIGINAL", "true").lower() != "false"
            optimize_image(dest, keep_original=keep)

    def _save_local(self, destination: str, secure_filename: bool, private: bool, validate: bool) -> str:
        """Save the file to the local filesystem and return the absolute path."""
        logger = logging.getLogger("asok.upload")
        safe_name = self._build_safe_name(secure_filename)
        dest = self._resolve_local_dest(destination, safe_name)
        dest = self._deduplicate_dest(dest)

        logger.debug("Saving uploaded file to: %s", dest)

        content_to_write = self._sanitize_svg_content(self.content, dest, validate, logger)
        self._write_local_file(dest, content_to_write, private)
        self._maybe_optimize_image(dest)

        self.filename = os.path.basename(dest)
        return dest

    def _validate_before_save(self, validate: bool, allowed_types: Optional[list[str]]) -> None:
        if validate and allowed_types is None:
            self._warn_no_allowed_types()

        if not validate:
            self._warn_validation_disabled()
        elif not self._validated:
            self.validate_mime_type(allowed_types)

    def save(
        self,
        destination: str,
        validate: bool = True,
        allowed_types: Optional[list[str]] = None,
        secure_filename: bool = True,
        private: bool = False,
    ) -> str:
        """Save the uploaded file to disk or cloud storage.

        Args:
            destination: Path relative to project root or absolute path.
            validate: If True, validate MIME type before saving (default: True)
            allowed_types: List of allowed MIME types for validation.
                          SECURITY WARNING: Should always be specified! Allowing all
                          types can lead to security vulnerabilities.
            secure_filename: If True, rename file with UUID for security (default: True)
            private: If True, restrict file permissions to owner-only (local 0o600) or
                     use private S3 ACL (default: False)

        Returns:
            The absolute path or URL where the file was saved.

        Raises:
            ValueError: If validation fails or path traversal is detected

        SECURITY: Always specify allowed_types to prevent malicious file uploads.
        Example: file.save('uploads/', allowed_types=['image/jpeg', 'image/png'])
        """
        self._validate_before_save(validate, allowed_types)

        # Route to S3 Storage if configured
        if os.environ.get("ASOK_STORAGE_BACKEND", "local").lower() == "s3":
            return self._save_to_s3(destination, secure_filename, private, validate)

        return self._save_local(destination, secure_filename, private, validate)

    def __getitem__(self, key: str) -> Union[str, bytes]:
        return {"filename": self.filename, "content": self.content}[key]
