from __future__ import annotations

import html
import logging
import os
import traceback
from typing import Any, Optional

from ..templates import render_template_string

logger = logging.getLogger("asok.errors")


class ErrorRendererMixin:
    """Mixin class for Asok that handles rendering of custom error pages or fallback headings."""

    def _setup_error_request(self, request: Any, code: int, message: Optional[str], error_file: str) -> None:
        """Configure the request object for rendering an error page."""
        request._current_page_file = error_file
        request.environ["asok.page_dir"] = os.path.dirname(error_file)
        request.page_id = f"error-{code}"
        request.meta.title(f"Error {code}")
        request.meta.description(message or "An error occurred.")
        request.params["title"] = f"Error {code}"
        request.params["description"] = message or "An error occurred."
        base_path = error_file.rsplit(".", 1)[0]
        for a_ext in ("css", "js"):
            p = f"{base_path}.{a_ext}"
            if os.path.isfile(p):
                request.scoped_assets[a_ext] = p

    def _render_error_template(self, request: Any, error_file: str, message: Optional[str]) -> str:
        """Render a template-based error page."""
        content = self._read_template(error_file)
        ctx = {
            "request": request,
            "__": request.__,
            "static": request.static,
            "get_flashed_messages": request.get_flashed_messages,
            "error_message": message,
            "title": getattr(request.meta, "_title", ""),
            "description": getattr(request.meta, "_description", "An error occurred."),
            "structured_data": getattr(request.meta, "_structured_data", None),
            "meta": request.meta,
            "nonce": getattr(request, "nonce", ""),
            **self._shared,
        }
        return render_template_string(content, ctx, root_dir=self._tpl_root)

    def _render_py_error(self, request: Any, error_file: str) -> Optional[str]:
        mod = self._load_module(error_file)
        if hasattr(mod, "render"):
            for k, v in self._shared.items():
                if k not in request.params:
                    request.params[k] = v
            return mod.render(request)
        return None

    def _try_render_error_file(
        self, request: Any, code: int, message: Optional[str], error_file: str, ext: str
    ) -> Optional[str]:
        """Try to render a single error page file. Returns rendered string or None on failure."""
        try:
            self._setup_error_request(request, code, message, error_file)
            if ext in (".py", ".pyc"):
                return self._render_py_error(request, error_file)
            return self._render_error_template(request, error_file, message)
        except Exception as e:
            logger.error(f"Error rendering custom {code} page: {e}\n{traceback.format_exc()}")
        return None

    def _render_error_page(
        self, request: Any, code: int, message: Optional[str] = None
    ) -> str:
        """Render a custom error page or return a fallback heading."""
        request.status_code(code)
        for ext in (".html", ".asok", ".py", ".pyc"):
            error_file = os.path.join(
                self.root_dir, self.dirs["PAGES"], str(code), self.config["INDEX"] + ext
            )
            if os.path.isfile(error_file):
                result = self._try_render_error_file(request, code, message, error_file, ext)
                if result is not None:
                    return result

        # SECURITY: HTML-escape message to prevent XSS
        fallback = f"<h1>{code}</h1>"
        if message:
            fallback += f"<p>{html.escape(message)}</p>"
        return fallback
