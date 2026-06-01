from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional, Union

from asok.utils.image import is_image, optimize_image

_RE_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


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

    # SECURITY: Detect double extensions (e.g., shell.php.png, malware.exe.jpg)
    # Split on dots and check for dangerous extensions in all positions
    parts = filename.split(".")
    if len(parts) > 2:  # Has more than one extension
        dangerous_exts = {
            "php",
            "phtml",
            "php3",
            "php4",
            "php5",
            "phps",
            "pht",
            "exe",
            "com",
            "bat",
            "cmd",
            "sh",
            "bash",
            "zsh",
            "csh",
            "pl",
            "py",
            "rb",
            "js",
            "jsp",
            "asp",
            "aspx",
            "cgi",
            "dll",
            "so",
            "dylib",
        }
        # Check all extensions except the last one
        for ext in parts[1:-1]:
            if ext.lower() in dangerous_exts:
                raise ValueError(
                    f"Double extension detected: '{filename}'. "
                    f"Files with dangerous intermediate extensions (e.g., .php, .exe) are not allowed."
                )

    # Fallback for empty names
    if not filename:
        filename = "upload"
    return filename


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

        # Verify magic bytes handling ambiguous formats
        detected_mime = None
        detected_exts = []

        # RIFF is ambiguous (WebP, WAV, AVI) - check sub-signature
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

        # ftyp is ambiguous (MP4, MOV, M4A, 3GP) - check type
        elif self.content.startswith(b"ftyp") or (
            len(self.content) >= 8 and self.content[4:8] == b"ftyp"
        ):
            # Extract ftyp type (4 bytes after "ftyp")
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

        # SECURITY: Fallback to generic MIME types for unrecognized sub-types
        # If specialized RIFF/ftyp checks didn't match, fall back to default MIME from magic bytes
        if not detected_mime:
            for magic, (mime, exts) in self._MAGIC_BYTES.items():
                if self.content.startswith(magic):
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

        # Verify that the extension matches the detected type
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
        # SECURITY WARNING: Log if allowed_types not specified
        if validate and allowed_types is None:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: File upload without allowed_types restriction. "
                "This allows any file type with valid magic bytes. "
                "Always specify allowed_types=['image/jpeg', 'image/png', ...] "
                "for secure file uploads."
            )

        # MIME type validation BEFORE writing to disk
        if not validate:
            logging.getLogger(__name__).warning(
                "SECURITY WARNING: File validation disabled (validate=False). "
                "This is dangerous and should only be used for trusted sources."
            )
        elif not self._validated:
            self.validate_mime_type(allowed_types)

        # Route to S3 Storage if configured
        if os.environ.get("ASOK_STORAGE_BACKEND", "local").lower() == "s3":
            from asok.core.storage import get_storage

            is_dir = destination.endswith(("/", "\\"))
            if secure_filename:
                import uuid

                _, ext = os.path.splitext(self.filename)
                safe_name = f"{uuid.uuid4()}{ext.lower()}"
            else:
                from asok.utils.security import secure_filename as sanitize_filename

                safe_name = sanitize_filename(self.filename)

            if is_dir:
                upload_to = destination.strip("/\\")
            else:
                upload_to = os.path.dirname(destination).strip("/\\")

            # SECURITY: Sanitize SVG files before uploading to S3
            content_to_upload = self.content
            _, ext = os.path.splitext(safe_name)
            if ext.lower() == ".svg" and allowed_types and "image/svg+xml" in allowed_types:
                from asok.utils.svg_sanitizer import sanitize_svg

                try:
                    content_to_upload = sanitize_svg(self.content)
                    logging.getLogger(__name__).debug("SVG file sanitized for S3: %s", safe_name)
                except ValueError as e:
                    raise ValueError(f"SVG sanitization failed: {e}")

            url = get_storage().save(safe_name, content_to_upload, upload_to)
            self.filename = safe_name
            return url

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
            from asok.utils.security import secure_filename as sanitize_filename

            safe_name = sanitize_filename(self.filename)

        # If it's a directory or already exists as one, append filename
        if is_dir or os.path.isdir(dest):
            os.makedirs(dest, exist_ok=True)
            dest = os.path.join(dest, safe_name)
        else:
            # Replace filename in destination with safe name
            dest_dir = os.path.dirname(dest)
            dest = os.path.join(dest_dir, safe_name)

        # SECURITY: Use logger instead of print to avoid path disclosure
        logger = logging.getLogger("asok.upload")
        logger.debug("Saving uploaded file to: %s", dest)

        # SECURITY: Resolve symlinks before path validation to prevent TOCTOU attacks
        # This prevents an attacker from replacing dest with a symlink after validation
        try:
            # Resolve all symlinks in both paths
            resolved_dest = os.path.realpath(dest)
            resolved_base = os.path.realpath(base_dir)
            common = os.path.commonpath([resolved_dest, resolved_base])
            if common != resolved_base:
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

        # SECURITY: Sanitize SVG files to remove JavaScript and other dangerous content
        content_to_write = self.content
        _, ext = os.path.splitext(dest)
        if ext.lower() == ".svg" and allowed_types and "image/svg+xml" in allowed_types:
            from asok.utils.svg_sanitizer import sanitize_svg

            try:
                content_to_write = sanitize_svg(self.content)
                logger.debug("SVG file sanitized successfully: %s", safe_name)
            except ValueError as e:
                raise ValueError(f"SVG sanitization failed: {e}")

        with open(dest, "wb") as f:
            f.write(content_to_write)

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
