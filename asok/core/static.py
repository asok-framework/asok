from __future__ import annotations

import hashlib
import mimetypes
import os
from typing import Any, Callable, Optional


class StaticMixin:
    """Mixin class for Asok that handles serving static files, caching hashes,
    and handling static HTTP requests.
    """

    def _static_hash(self, filepath: str) -> Optional[str]:
        """Compute and cache an MD5 hash of a static file for versioning."""
        if filepath in self._static_hashes:
            return self._static_hashes[filepath]
        full_path = os.path.join(self._partials_path, filepath.lstrip("/"))
        if not os.path.isfile(full_path):
            return None
        with open(full_path, "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()[:8]
        self._static_hashes[filepath] = h
        return h

    def _serve_static(
        self,
        static_path: str,
        start_response: Callable,
        environ: Optional[dict[str, Any]] = None,
    ) -> Optional[list[bytes]]:
        """Serve a static file with appropriate mime types and caching headers."""
        import time

        debug = self.config.get("DEBUG")

        if not debug and static_path in self._static_cache:
            content, mimetype, etag, _ = self._static_cache[static_path]
            # Update last access time for LRU
            self._static_cache[static_path] = (content, mimetype, etag, time.time())
        else:
            if not os.path.isfile(static_path):
                return None
            mimetype, _ = mimetypes.guess_type(static_path)
            mimetype = mimetype or "application/octet-stream"
            with open(static_path, "rb") as f:
                content = f.read()
            etag = hashlib.md5(content).hexdigest()
            if not debug:
                content_size = len(content)
                # LRU eviction: if adding this file exceeds cache size, evict oldest
                if self._static_cache_size + content_size > self._static_cache_max:
                    # Evict least recently used files until we have space
                    while (
                        self._static_cache
                        and self._static_cache_size + content_size
                        > self._static_cache_max
                    ):
                        # Find oldest entry by last access time
                        oldest_path = min(
                            self._static_cache.keys(),
                            key=lambda k: self._static_cache[k][3],
                        )
                        oldest_content, _, _, _ = self._static_cache[oldest_path]
                        self._static_cache_size -= len(oldest_content)
                        del self._static_cache[oldest_path]

                # Add new file to cache with current timestamp
                self._static_cache[static_path] = (content, mimetype, etag, time.time())
                self._static_cache_size += content_size

        if environ and not debug:
            if_none_match = environ.get("HTTP_IF_NONE_MATCH", "").strip()
            if if_none_match and if_none_match == etag:
                start_response(
                    "304 Not Modified",
                    [
                        ("ETag", etag),
                        ("Cache-Control", "public, max-age=86400"),
                    ],
                )
                return [b""]

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

        start_response("200 OK", headers)
        return [content]

    def _handle_static_request(
        self, request: Any, environ: dict[str, Any], start_response: Callable
    ) -> Optional[list[bytes]]:
        """Check if request targets static directories and serve the static file if it exists."""
        parts = [p for p in request.path.split("/") if p]
        if parts and parts[0] in self._static_dirs:
            # SECURITY: Use commonpath to prevent path traversal via symlinks or edge cases
            base_path = os.path.abspath(self._partials_path)
            static_path = os.path.abspath(os.path.join(base_path, *parts))
            try:
                if os.path.commonpath([static_path, base_path]) != base_path:
                    body = self._render_error_page(request, 403)
                    start_response(
                        "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
                    )
                    return [body.encode("utf-8")]
            except ValueError:
                # Paths on different drives (Windows) - deny access
                body = self._render_error_page(request, 403)
                start_response(
                    "403 Forbidden", [("Content-Type", "text/html; charset=utf-8")]
                )
                return [body.encode("utf-8")]
            return self._serve_static(static_path, start_response, environ)
        return None
