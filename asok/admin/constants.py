from __future__ import annotations

# Admin permission verbs available per model
ADMIN_VERBS = [
    "view",
    "add",
    "edit",
    "delete",
    "export",
]

# Above this many target rows, FK fields render as autocomplete instead of <select>
FK_AUTOCOMPLETE_THRESHOLD = 200

# Whitelist of allowed MIME types for file uploads in admin
# Add more types as needed, but be restrictive for security
ALLOWED_UPLOAD_MIMES = {
    # Images
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",  # SECURITY: Safe with automatic sanitization in UploadedFile.save()
    "image/bmp",
    "image/x-icon",  # Favicon
    # Documents
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-excel",  # .xls
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    # Videos
    "video/mp4",
    "video/mpeg",
    "video/quicktime",  # .mov
    "video/x-msvideo",  # .avi
    "video/webm",
    "video/x-matroska",  # .mkv
    "video/x-flv",  # .flv
    # Audio
    "audio/mpeg",  # .mp3
    "audio/wav",
    "audio/x-wav",
    "audio/ogg",
    "audio/webm",
    "audio/aac",
    "audio/flac",
    "audio/x-m4a",  # .m4a
    # Archives
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",  # .rar
    "application/x-7z-compressed",  # .7z
    "application/x-tar",  # .tar
    "application/gzip",  # .gz
}

# Extensions to block regardless of MIME type
BLOCKED_EXTENSIONS = {
    ".exe",
    ".bat",
    ".cmd",
    ".com",
    ".pif",
    ".scr",  # Windows executables
    ".php",
    ".php3",
    ".php4",
    ".php5",
    ".phtml",  # PHP scripts
    ".sh",
    ".bash",
    ".zsh",
    ".fish",  # Shell scripts
    ".py",
    ".pyc",
    ".pyo",  # Python scripts
    ".js",
    ".mjs",  # JavaScript (except in specific contexts)
    ".jar",
    ".war",  # Java archives
    ".app",
    ".dmg",
    ".pkg",  # macOS executables/installers
}

_IMAGE_EXTS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",  # SECURITY: Safe with automatic sanitization in UploadedFile.save()
    ".avif",
    ".bmp",
    ".ico",
    ".tiff",
    ".tif",
)
