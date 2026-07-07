from __future__ import annotations

import gzip as gzip_mod
import io
import logging
import traceback
from typing import TYPE_CHECKING, Callable, Iterator

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

    def _get_write_func(self) -> Callable[[bytes], bytes]:
        write = self.request.environ.get("asok.write")
        if not write:
            return lambda data: data
        return write

    def _should_minify_html(self) -> bool:
        should_minify = self.app.config.get("HTML_MINIFY")
        if should_minify is None:
            return not self.app.config.get("DEBUG", False)
        return should_minify

    def _prepare_content(self, should_minify: bool) -> str:
        full_content = "".join(self.generator)
        if should_minify and full_content:
            full_content = minify_html(full_content)
        return self.app._inject_assets(
            full_content, self.request, self.nonce, stream=True, only_scripts=False
        )

    def _gzip_if_supported(self, encoded: bytes) -> bytes:
        if (
            self.app.config.get("GZIP", False)
            and "gzip" in self.request.environ.get("HTTP_ACCEPT_ENCODING", "").lower()
        ):
            buf = io.BytesIO()
            with gzip_mod.GzipFile(fileobj=buf, mode="wb") as f:
                f.write(encoded)
            return buf.getvalue()
        return encoded

    def _save_session(self) -> None:
        if self.request._session is not None and self.request._session.modified:
            self.app._session_store.save(
                self.request._session.sid, self.request._session
            )

    def __iter__(self) -> Iterator[bytes]:
        write = self._get_write_func()
        should_minify = self._should_minify_html()

        try:
            final_content = self._prepare_content(should_minify)
            encoded = final_content.encode("utf-8")
            encoded = self._gzip_if_supported(encoded)
            yield write(encoded)
        except Exception as e:
            # SECURITY: Log errors server-side only, never expose in HTML comments
            # Even in DEBUG mode, error details in client-visible comments leak information
            logger.error(f"Streamer Error: {e}\n{traceback.format_exc()}")
        finally:
            self._save_session()
