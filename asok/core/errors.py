from __future__ import annotations

import html
import logging
import os
import traceback
from typing import Any, Optional

from ..templates import render_template_string

logger = logging.getLogger("asok.errors")


def _safe_repr(val: Any) -> str:
    try:
        return html.escape(repr(val))
    except Exception:
        return "[unrenderable]"


class ErrorRendererMixin:
    """Mixin class for Asok that handles rendering of custom error pages or fallback headings."""

    def _setup_error_request(
        self, request: Any, code: int, message: Optional[str], error_file: str
    ) -> None:
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

    def _render_error_template(
        self, request: Any, error_file: str, message: Optional[str]
    ) -> str:
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
        }
        if hasattr(request, "_inject_shared_vars"):
            request._inject_shared_vars(ctx)
        else:
            ctx.update(self._shared)

        return render_template_string(
            content, ctx, root_dir=self._tpl_root, template_name=error_file
        )

    def _render_py_error(self, request: Any, error_file: str) -> Optional[str]:
        mod = self._load_module(error_file)
        if hasattr(mod, "render"):
            for k in self._shared:
                if k not in request.params:
                    try:
                        request.params[k] = request.shared(k)
                    except Exception:
                        request.params[k] = self._shared[k]
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
            logger.error(
                f"Error rendering custom {code} page: {e}\n{traceback.format_exc()}"
            )
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
                result = self._try_render_error_file(
                    request, code, message, error_file, ext
                )
                if result is not None:
                    return result

        # SECURITY: HTML-escape message to prevent XSS
        fallback = f"<h1>{code}</h1>"
        if message:
            fallback += f"<p>{html.escape(message)}</p>"
        return fallback

    def _render_debug_exception_page(self, request: Any, e: Exception) -> str:
        """Render a clean Flask-style debug HTML report for unhandled Python exceptions."""
        import html
        import traceback

        tb_html = html.escape(traceback.format_exc())
        exc_name = type(e).__name__
        error_msg = html.escape(str(e))
        ctx_section = _build_debug_context_table(request)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{html.escape(exc_name)} — Asok Debugger</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f8f9fa;
            color: #212529;
            margin: 0;
            padding: 0 0 50px 0;
            line-height: 1.5;
        }}
        .header {{
            background: #d9534f;
            color: #ffffff;
            padding: 24px 32px;
            border-bottom: 2px solid #c9302c;
        }}
        .header h1 {{
            margin: 0 0 8px 0;
            font-size: 1.6rem;
            font-weight: 700;
        }}
        .header .subtitle {{
            font-size: 1.05rem;
            opacity: 0.95;
            font-family: Consolas, Menlo, Monaco, monospace;
        }}
        .container {{
            max-width: 1000px;
            margin: 30px auto 0 auto;
            padding: 0 20px;
        }}
        .card {{
            background: #ffffff;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        h3 {{
            margin-top: 0;
            margin-bottom: 15px;
            font-size: 1.1rem;
            color: #343a40;
            border-bottom: 1px solid #e9ecef;
            padding-bottom: 8px;
        }}
        pre {{
            background: #f1f3f5;
            color: #212529;
            padding: 16px;
            border-radius: 4px;
            overflow-x: auto;
            font-family: Consolas, Menlo, Monaco, "Courier New", monospace;
            font-size: 0.9rem;
            border: 1px solid #ced4da;
            margin: 0;
            line-height: 1.5;
        }}
        td, th {{
            padding: 8px 12px;
            border-bottom: 1px solid #dee2e6;
            text-align: left;
        }}
        code {{
            font-family: Consolas, Menlo, Monaco, monospace;
            color: #d63384;
            background: #e9ecef;
            padding: 2px 5px;
            border-radius: 3px;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{html.escape(exc_name)}</h1>
        <div class="subtitle">{error_msg}</div>
    </div>
    <div class="container">
        <div class="card">
            <h3>Traceback</h3>
            <pre>{tb_html}</pre>
        </div>
        <div class="card">
            <h3>Request Context</h3>
            {ctx_section}
        </div>
    </div>
</body>
</html>
"""


def _extract_dict_rows(prefix: str, data: dict) -> list[str]:
    import html

    return [
        f"<tr><td><strong>{prefix}: {html.escape(k)}</strong></td><td><code>{_safe_repr(v)}</code></td></tr>"
        for k, v in sorted(data.items())
    ]


def _build_debug_context_table(request: Any) -> str:
    import html

    ctx_details = [
        f"<tr><td><strong>Request URL</strong></td><td><code>{html.escape(getattr(request, 'method', 'GET'))} {html.escape(getattr(request, 'path', '/'))}</code></td></tr>"
    ]
    if hasattr(request, "params"):
        ctx_details.extend(_extract_dict_rows("Param", request.params))
    if hasattr(request, "form"):
        ctx_details.extend(_extract_dict_rows("Form Data", request.form))

    ctx_table = "".join(ctx_details)
    return (
        f"<table style='width: 100%; border-collapse: collapse; font-size: 0.9rem;'><thead><tr style='background: #f8f9fa;'><th style='padding: 8px 12px; border-bottom: 1px solid #dee2e6; text-align: left;'>Property / Variable</th><th style='padding: 8px 12px; border-bottom: 1px solid #dee2e6; text-align: left;'>Value</th></tr></thead><tbody>{ctx_table}</tbody></table>"
        if ctx_table
        else "<p style='color: #6c757d; font-style: italic;'>No request details set.</p>"
    )
