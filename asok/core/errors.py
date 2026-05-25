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
                try:
                    shared_vars = self._shared

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

                    if ext in (".py", ".pyc"):
                        mod = self._load_module(error_file)
                        if hasattr(mod, "render"):
                            for k, v in shared_vars.items():
                                if k not in request.params:
                                    request.params[k] = v
                            return mod.render(request)
                    else:
                        content = self._read_template(error_file)
                        ctx = {
                            "request": request,
                            "__": request.__,
                            "static": request.static,
                            "get_flashed_messages": request.get_flashed_messages,
                            "error_message": message,
                            "title": getattr(request.meta, "_title", f"Error {code}"),
                            "description": getattr(
                                request.meta, "_description", "An error occurred."
                            ),
                            "structured_data": getattr(
                                request.meta, "_structured_data", None
                            ),
                            "meta": request.meta,
                            "nonce": getattr(request, "nonce", ""),
                            **shared_vars,
                        }
                        return render_template_string(
                            content, ctx, root_dir=self._tpl_root
                        )
                except Exception as e:
                    logger.error(
                        f"Error rendering custom {code} page: {e}\n{traceback.format_exc()}"
                    )
                    pass

        # SECURITY: HTML-escape message to prevent XSS
        fallback = f"<h1>{code}</h1>"
        if message:
            fallback += f"<p>{html.escape(message)}</p>"
        return fallback
