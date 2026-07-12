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
        """Render a detailed, premium debug HTML report for unhandled Python exceptions (DEBUG mode only)."""
        import html
        import traceback

        tb_html = html.escape(traceback.format_exc())
        error_msg = html.escape(f"{type(e).__name__}: {str(e)}")

        ctx_details = []
        ctx_details.append(
            f"<tr><td><strong>Request URL</strong></td><td><code>{html.escape(request.method)} {html.escape(request.path)}</code></td></tr>"
        )

        for k, v in sorted(request.params.items()):
            ctx_details.append(
                f"<tr><td><strong>Param: {html.escape(k)}</strong></td><td><code>{_safe_repr(v)}</code></td></tr>"
            )

        for k, v in sorted(request.form.items()):
            ctx_details.append(
                f"<tr><td><strong>Form Data: {html.escape(k)}</strong></td><td><code>{_safe_repr(v)}</code></td></tr>"
            )

        ctx_table = "".join(ctx_details)
        ctx_section = (
            f"<table><thead><tr><th>Property / Variable</th><th>Value</th></tr></thead><tbody>{ctx_table}</tbody></table>"
            if ctx_table
            else "<p style='color: #9ca3af; font-style: italic;'>No request details set.</p>"
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{html.escape(type(e).__name__)} — Asok Debugger</title>
    <style>
        body {{
            font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background-color: #0b0f19;
            color: #f1f5f9;
            margin: 0;
            padding: 40px 20px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 4px 6px -4px rgba(0, 0, 0, 0.5);
            overflow: hidden;
        }}
        .header {{
            background: #ef4444;
            color: #fff;
            padding: 24px 32px;
            border-bottom: 1px solid #dc2626;
        }}
        .header h1 {{
            margin: 0;
            font-size: 1.5rem;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
        }}
        .content {{
            padding: 32px;
        }}
        .error-message {{
            background: #271c1c;
            border: 1px solid #7f1d1d;
            color: #fca5a5;
            padding: 16px 20px;
            border-radius: 8px;
            font-size: 1.1rem;
            font-weight: 500;
            margin-bottom: 24px;
            word-break: break-word;
        }}
        .section-title {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #e2e8f0;
            margin: 24px 0 12px 0;
            border-bottom: 1px solid #374151;
            padding-bottom: 8px;
        }}
        pre {{
            background: #1e293b;
            padding: 20px;
            border-radius: 8px;
            overflow-x: auto;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
            font-size: 0.9rem;
            color: #cbd5e1;
            border: 1px solid #334155;
            margin: 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
            font-size: 0.9rem;
        }}
        th, td {{
            text-align: left;
            padding: 10px 12px;
            border-bottom: 1px solid #1f2937;
        }}
        th {{
            background: #1f2937;
            color: #9ca3af;
            font-weight: 600;
        }}
        tr:hover td {{
            background: #1e293b;
        }}
        code {{
            font-family: monospace;
            color: #a855f7;
            background: #2e1065;
            padding: 2px 6px;
            border-radius: 4px;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            background: #f87171;
            color: #7f1d1d;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span class="badge">Exception</span> Unhandled error in controller</h1>
        </div>
        <div class="content">
            <div class="error-message">
                {error_msg}
            </div>

            <h3 class="section-title">Traceback</h3>
            <pre>{tb_html}</pre>

            <h3 class="section-title">Request Context & Parameters</h3>
            {ctx_section}
        </div>
    </div>
</body>
</html>
"""
