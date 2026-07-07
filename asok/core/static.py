from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Any, Callable, Optional

_STATIC_MIME_FALLBACKS = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
}


class StaticMixin:
    """Mixin class for Asok that handles serving static files, caching hashes,
    and handling static HTTP requests.
    """

    def _static_hash(self, filepath: str) -> Optional[str]:
        """Compute and cache an MD5 hash of a static file for versioning."""
        if filepath in self._static_hashes:
            return self._static_hashes[filepath]
        search_paths = getattr(self, "_static_search_paths", [self._partials_path])
        for base_dir in search_paths:
            full_path = os.path.join(base_dir, filepath.lstrip("/"))
            if os.path.isfile(full_path):
                with open(full_path, "rb") as f:
                    h = hashlib.md5(f.read()).hexdigest()[:8]
                self._static_hashes[filepath] = h
                return h
        return None

    def _resolve_mimetype(self, static_path: str) -> str:
        """Guess the MIME type for a static file path."""
        mimetype, _ = mimetypes.guess_type(static_path)
        if mimetype:
            return mimetype
        _, ext = os.path.splitext(static_path)
        return _STATIC_MIME_FALLBACKS.get(ext, "application/octet-stream")

    def _evict_static_cache(self, content_size: int) -> None:
        """Evict LRU entries until there is space for content_size bytes.

        O(1) per eviction via OrderedDict.popitem(last=False) — items are
        ordered from LRU (front) to MRU (back) by move_to_end() on each hit.
        """
        while (
            self._static_cache
            and self._static_cache_size + content_size > self._static_cache_max
        ):
            _, (oldest_content, _, _) = self._static_cache.popitem(last=False)
            self._static_cache_size -= len(oldest_content)

    def _read_and_cache_static(
        self, static_path: str, debug: bool
    ) -> tuple[bytes, str, str]:
        """Read a static file from disk and cache it (in non-debug mode)."""
        mimetype = self._resolve_mimetype(static_path)
        with open(static_path, "rb") as f:
            content = f.read()
        etag = hashlib.md5(content).hexdigest()
        if not debug:
            content_size = len(content)
            if self._static_cache_size + content_size > self._static_cache_max:
                self._evict_static_cache(content_size)
            self._static_cache[static_path] = (content, mimetype, etag)
            self._static_cache_size += content_size
        return content, mimetype, etag

    def _build_static_response_headers(
        self, mimetype: str, content: bytes, etag: str, debug: bool
    ) -> list:
        """Build the HTTP response headers for a static file."""
        headers = [
            ("Content-Type", mimetype),
            ("Content-Length", str(len(content))),
            (
                "Cache-Control",
                "public, max-age=86400" if not debug else "no-cache, no-store",
            ),
        ]
        if not debug:
            headers.append(("ETag", etag))
        return headers

    def _get_static_content(
        self, static_path: str, debug: bool
    ) -> Optional[tuple[bytes, str, str]]:
        if not debug and static_path in self._static_cache:
            # Promote to MRU position for LRU ordering.
            self._static_cache.move_to_end(static_path)
            return self._static_cache[static_path]

        if not os.path.isfile(static_path):
            return None
        return self._read_and_cache_static(static_path, debug)

    def _is_static_304(
        self, environ: Optional[dict[str, Any]], etag: str, debug: bool
    ) -> bool:
        if not environ or debug:
            return False
        if_none_match = environ.get("HTTP_IF_NONE_MATCH", "").strip()
        return bool(if_none_match) and if_none_match == etag

    def _serve_static(
        self,
        static_path: str,
        start_response: Callable,
        environ: Optional[dict[str, Any]] = None,
    ) -> Optional[list[bytes]]:
        """Serve a static file with appropriate mime types and caching headers."""
        debug = self.config.get("DEBUG")
        res = self._get_static_content(static_path, debug)
        if res is None:
            return None
        content, mimetype, etag = res

        if self._is_static_304(environ, etag, debug):
            start_response(
                "304 Not Modified",
                [("ETag", etag), ("Cache-Control", "public, max-age=86400")],
            )
            return [b""]

        headers = self._build_static_response_headers(mimetype, content, etag, debug)
        start_response("200 OK", headers)
        return [content]

    def _check_static_path_traversal(
        self, static_path: str, base_path: str, request: Any, start_response: Callable
    ) -> Optional[list[bytes]]:
        """Return a 403 response if static_path is outside base_path, else None."""
        try:
            if os.path.commonpath([static_path, base_path]) != base_path:
                body = self._render_error_page(request, 403)
                start_response(
                    "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
                )
                return [body.encode("utf-8")]
        except ValueError:
            body = self._render_error_page(request, 403)
            start_response(
                "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
            )
            return [body.encode("utf-8")]
        return None

    def _get_static_parts(self, request: Any) -> Optional[list[str]]:
        parts = [p for p in request.path.split("/") if p]
        if parts and parts[0] in self._static_dirs:
            return parts
        return None

    def _handle_static_request(
        self, request: Any, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        """Check if request targets static directories and serve the static file if it exists."""
        parts = self._get_static_parts(request)
        if not parts:
            return None
        search_paths = getattr(self, "_static_search_paths", [self._partials_path])
        for base_dir in search_paths:
            base_path = os.path.abspath(base_dir)
            # SECURITY: Enforce containment inside the specific static subdirectory (base_path/parts[0])
            # to prevent directory traversal via URL paths like /css/../template.html
            subdir_path = os.path.abspath(os.path.join(base_path, parts[0]))
            static_path = os.path.abspath(os.path.join(subdir_path, *parts[1:]))
            traversal = self._check_static_path_traversal(
                static_path, subdir_path, request, start_response
            )
            if traversal is not None:
                return traversal
            if os.path.isfile(static_path):
                return self._serve_static(static_path, start_response, environ)
        return None
