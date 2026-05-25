from __future__ import annotations

import gzip as gzip_mod
import io
import logging
import traceback
from typing import TYPE_CHECKING, Iterator

from ..utils.minify import minify_html

if TYPE_CHECKING:
    from ..request import Request
    from .asok import Asok

logger = logging.getLogger("asok.streamer")


class SmartStreamer:
    """
    Advanced streaming response wrapper that handles:
    1. Automatic asset injection (JS/CSS)
    2. HTML minification on the fly
    3. Proper buffer management to avoid cutting tags
    4. SPA block extraction
    """

    def __init__(self, generator: Iterator[str], request: Request, app: Asok):
        self.generator = generator
        self.request = request
        self.app = app
        self.nonce = request.nonce
        self.buffer_str = ""

    def __iter__(self) -> Iterator[bytes]:
        write = self.request.environ.get("asok.write")
        if not write:

            def write_fallback(data: bytes):
                return data

            write = write_fallback

        should_minify = self.app.config.get("HTML_MINIFY")
        if should_minify is None:
            should_minify = not self.app.config.get("DEBUG", False)

        def finalize(text):
            if not should_minify or not text:
                return text
            return minify_html(text)

        try:
            full_content = ""
            for chunk_str in self.generator:
                full_content += chunk_str

            full_content = finalize(full_content)

            final_content = self.app._inject_assets(
                full_content, self.request, self.nonce, stream=True, only_scripts=False
            )

            encoded = final_content.encode("utf-8")

            if (
                self.app.config.get("GZIP", False)
                and "gzip"
                in self.request.environ.get("HTTP_ACCEPT_ENCODING", "").lower()
            ):
                buf = io.BytesIO()
                with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
                    f.write(encoded)
                encoded = buf.getvalue()

            yield write(encoded)

        except Exception as e:
            # SECURITY: Log errors server-side only, never expose in HTML comments
            # Even in DEBUG mode, error details in client-visible comments leak information
            logger.error(f"Streamer Error: {e}\n{traceback.format_exc()}")
            # Return empty response on streaming error (error already logged)
            pass

        finally:
            if self.request._session is not None and self.request._session.modified:
                self.app._session_store.save(
                    self.request._session.sid, self.request._session
                )
