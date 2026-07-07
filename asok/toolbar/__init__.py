from __future__ import annotations

import html as _html
import json
import os
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..core import Asok
    from ..request import Request

__all__ = ["DeveloperToolbar"]


class DeveloperToolbar:
    """Developer Toolbar for Asok.

    Decoupled module for real-time inspection of framework state.
    Aligned with the admin/ and api/ design system.
    """

    def __init__(self, request: Request, app: Asok):
        self.request = request
        self.app = app
        self.config = app.config
        self.base_path = os.path.dirname(__file__)

    def _validate_path_parts(self, parts: tuple) -> bool:
        """Return True if all path parts are safe (no traversal, valid length)."""
        for part in parts:
            if not self._is_safe_part(part):
                return False
        return True

    def _is_safe_part(self, part: Any) -> bool:
        if not isinstance(part, str) or not part:
            return False
        if len(part) > 100:
            return False
        return self._has_no_traversal_chars(part)

    def _has_no_traversal_chars(self, part: str) -> bool:
        return ".." not in part and "/" not in part and "\\" not in part

    def _is_within_base(self, path: str) -> bool:
        """Return True if path resolves to be within self.base_path."""
        try:
            abs_base = os.path.abspath(self.base_path)
            abs_path = os.path.abspath(path)
            return os.path.commonpath([abs_path, abs_base]) == abs_base
        except (ValueError, OSError):
            return False

    def _resolve_min_path(self, parts: tuple) -> tuple:
        """If the last part is a CSS/JS file, resolve the .min version if it exists."""
        if not parts:
            return parts
        filename = parts[-1]
        base, ext = os.path.splitext(filename)
        if base.endswith(".min") or ext not in (".js", ".css"):
            return parts
        return self._find_min_file(parts, base, ext)

    def _find_min_file(self, parts: tuple, base: str, ext: str) -> tuple:
        min_filename = f"{base}.min{ext}"
        min_parts = list(parts[:-1]) + [min_filename]
        min_path = os.path.join(self.base_path, *min_parts)
        if self._is_within_base(min_path) and os.path.exists(min_path):
            return tuple(min_parts)
        return parts

    def _validate_file_path(self, path: str) -> bool:
        if not self._is_within_base(path) or not os.path.exists(path):
            return False
        try:
            return os.path.getsize(path) <= 1_000_000
        except OSError:
            return False

    def _read_file(self, *parts: str) -> str:
        """Read a file. Package only contains minified files, so always use .min versions for CSS/JS.

        SECURITY: Path traversal protection via commonpath validation.
        """
        if not self._validate_path_parts(parts):
            return ""

        parts = self._resolve_min_path(parts)
        path = os.path.join(self.base_path, *parts)

        if not self._validate_file_path(path):
            return ""

        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _render_sql_row(
        self, i: int, entry: dict, row_class: str, tc_warn: str, tc_normal: str
    ) -> str:
        query = entry.get("sql", "")
        params = entry.get("params", "")
        duration = entry.get("duration", 0)

        if len(query) > 10_000:
            query = query[:10_000] + "... [truncated]"
        params_str = str(params)
        if len(params_str) > 1_000:
            params_str = params_str[:1_000] + "... [truncated]"

        tc = tc_warn if duration > 50 else tc_normal
        return (
            f'<tr class="{row_class}">'
            f'<td style="color:var(--fg-3)">{i + 1}</td>'
            f"<td>"
            f'<div class="asok-query-sql">{_html.escape(query)}</div>'
            f'<div class="asok-query-params">Params: {_html.escape(params_str)}</div>'
            f"</td>"
            f'<td style="text-align:right; padding-right:24px"><span class="{tc}">{duration:.2f}ms</span></td>'
            f"</tr>"
        )

    def _build_redir_sql_rows(self, redir_sql: list, redir_stats: dict) -> str:
        redir_method = redir_stats.get("method", "")
        redir_path = redir_stats.get("path", "")
        q_label = "query" if len(redir_sql) == 1 else "queries"
        html = (
            f'<tr class="asok-redir-banner">'
            f'<td colspan="3">'
            f"&#8593; REDIRECT FROM&nbsp;"
            f'<span class="asok-method-badge">{redir_method}</span>'
            f"&nbsp;{_html.escape(redir_path)}"
            f"&nbsp;&#x2014;&nbsp;{len(redir_sql)} {q_label}"
            f"</td></tr>"
        )
        for i, entry in enumerate(redir_sql):
            html += self._render_sql_row(
                i, entry, "asok-redir-row", "asok-time-slow", "asok-time-warn"
            )
        return html

    def _build_sql_rows(
        self, redir_sql: list, current_sql: list, redir_stats: Optional[dict]
    ) -> str:
        sql_rows = ""
        if redir_sql and redir_stats:
            sql_rows += self._build_redir_sql_rows(redir_sql, redir_stats)

        for i, entry in enumerate(current_sql):
            sql_rows += self._render_sql_row(
                i, entry, "", "asok-time-slow", "asok-time-fast"
            )

        if not sql_rows:
            sql_rows = (
                '<tr><td colspan="3">'
                '<div class="asok-empty" style="padding:40px 0;">'
                '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>'
                '<span class="asok-empty-text">No SQL queries recorded.</span>'
                "</div>"
                "</td></tr>"
            )
        return sql_rows

    def _build_session_data(self) -> tuple[str, int]:
        try:
            session_dict = dict(self.request.session)
            if len(session_dict) > 1000:
                session_dict = {
                    "error": f"Too many keys ({len(session_dict)}), display limited"
                }
        except Exception:
            session_dict = {}

        try:
            session_json = json.dumps(session_dict, indent=2)
            if len(session_json) > 100_000:
                session_json = json.dumps(
                    {"error": "Session data too large to display"}, indent=2
                )
            session_data = _html.escape(session_json)
        except (TypeError, ValueError):
            session_data = _html.escape(
                json.dumps({"error": "Could not serialize session"}, indent=2)
            )

        return session_data, len(session_dict)

    def _get_request_payload(self, req_info: dict) -> None:
        try:
            if self.request.content_type == "application/json":
                req_info["JSON Payload"] = self.request.json
        except Exception:
            pass

    def _build_request_data(self) -> str:
        try:
            req_info = {
                "URL Parameters": dict(self.request.args),
                "Body / Payload": dict(self.request.form),
            }
            self._get_request_payload(req_info)
            req_info.update(
                {
                    "Cookies": dict(self.request.cookies_dict),
                    "IP": self.request.ip,
                }
            )

            req_json = json.dumps(req_info, indent=2)
            if len(req_json) > 100_000:
                req_json = json.dumps(
                    {"error": "Request data too large to display"}, indent=2
                )
            return _html.escape(req_json)
        except (TypeError, ValueError) as e:
            return _html.escape(
                json.dumps(
                    {"error": f"Could not serialize request data: {str(e)}"}, indent=2
                )
            )
        except Exception:
            return _html.escape(
                json.dumps({"error": "Could not serialize request data"}, indent=2)
            )

    def _build_redir_info_html(self, redir_stats: Optional[dict]) -> str:
        if not redir_stats:
            return ""
        rm = redir_stats.get("method", "")
        rp = redir_stats.get("path", "")
        ra = redir_stats.get("args", {})
        rf = redir_stats.get("form", {})
        prev = {}
        if ra:
            prev["Query Parameters"] = ra
        if rf:
            prev["Body / Payload"] = rf
        return (
            f'<div class="asok-redir-block">'
            f'<div class="asok-redir-block-header">'
            f"&#8593; Previous Request &mdash; "
            f'<span class="asok-method-badge" style="background:rgba(217,119,6,0.3);color:var(--warn-fg);">{rm}</span>'
            f"&nbsp;{_html.escape(rp)}"
            f"</div>"
            f"<pre>{_html.escape(json.dumps(prev, indent=2))}</pre>"
            f"</div>"
        )

    def _build_template_data(self) -> tuple[str, str]:
        tpl_list = getattr(self.request, "_asok_templates", [])
        blk_list = getattr(self.request, "_asok_blocks", [])
        tpl_info = {
            "Main Template": tpl_list[0] if tpl_list else "None",
            "All Templates": tpl_list,
            "Partial Blocks": blk_list,
            "WS Components": [],
        }
        return json.dumps(tpl_info), json.dumps(tpl_info, indent=2)

    def _get_redir_stats(self) -> Optional[dict]:
        try:
            if hasattr(self.request, "session"):
                return self.request.session.pop("_asok_redir_stats", None)
        except Exception:
            pass
        return None

    def _get_pruned_sql_logs(self, redir_stats: Optional[dict]) -> tuple[list, list]:
        current_sql = getattr(self.request, "_asok_sql_log", [])
        redir_sql = redir_stats.get("sql_log", []) if redir_stats else []

        MAX_SQL_QUERIES = 1000
        if len(current_sql) > MAX_SQL_QUERIES:
            current_sql = current_sql[:MAX_SQL_QUERIES]
        if len(redir_sql) > MAX_SQL_QUERIES:
            redir_sql = redir_sql[:MAX_SQL_QUERIES]
        return redir_sql, current_sql

    def render(self) -> str:
        """Render the toolbar by combining templates and assets."""
        nonce = getattr(self.request, "nonce", "")
        redir_stats = self._get_redir_stats()
        redir_sql, current_sql = self._get_pruned_sql_logs(redir_stats)

        sql_rows = self._build_sql_rows(redir_sql, current_sql, redir_stats)
        total_count = len(redir_sql) + len(current_sql)

        session_data, session_keys = self._build_session_data()
        request_data = self._build_request_data()
        redir_info_html = self._build_redir_info_html(redir_stats)

        tpl_json, tpl_raw_data = self._build_template_data()
        tpl_data = _html.escape(tpl_raw_data)

        css = self._read_file("static", "toolbar.css")
        js = self._read_file("static", "toolbar.js")
        template = self._read_file("templates", "toolbar.html")

        content = template.replace("[[css]]", css).replace("[[js]]", js)

        replacements = {
            "[[nonce]]": nonce,
            "[[version]]": self.config.get("VERSION", self.app.version),
            "[[sql_count]]": str(total_count),
            "[[sql_rows]]": sql_rows,
            "[[session_data]]": session_data,
            "[[session_keys]]": str(session_keys),
            "[[request_data]]": request_data,
            "[[method]]": _html.escape(self.request.method),
            "[[path]]": _html.escape(self.request.path),
            "[[tpl_data]]": tpl_data,
            "[[tpl_json]]": tpl_json,
            "[[redir_info_html]]": redir_info_html,
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        return content

    def _is_eligible_for_injection(self, html_content: str) -> bool:
        if not html_content or len(html_content) > 10_000_000:
            return False
        return "</body>" in html_content

    def inject(self, html_content: str) -> str:
        """Inject the toolbar into the HTML response.

        SECURITY: Size limits prevent DoS via extremely large HTML.
        """
        if not self._is_eligible_for_injection(html_content):
            return html_content

        try:
            toolbar_html = self.render()
            # SECURITY: Limit toolbar size
            if len(toolbar_html) > 1_000_000:
                return html_content
            return html_content.replace("</body>", toolbar_html + "</body>")
        except Exception:
            # SECURITY: Silently fail if toolbar rendering fails
            return html_content
