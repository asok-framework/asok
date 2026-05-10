from __future__ import annotations

import html as _html
import json
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..core import Asok
    from ..request import Request


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

    def _read_file(self, *parts: str) -> str:
        path = os.path.join(self.base_path, *parts)
        if not os.path.exists(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def render(self) -> str:
        """Render the toolbar by combining templates and assets."""
        nonce = getattr(self.request, "nonce", "")

        # 1. Collect redirect stats from session
        redir_stats = None
        try:
            if hasattr(self.request, "session"):
                redir_stats = self.request.session.pop("_asok_redir_stats", None)
        except Exception:
            pass

        # 2. Build SQL rows
        current_sql = getattr(self.request, "_asok_sql_log", [])
        redir_sql = redir_stats.get("sql_log", []) if redir_stats else []

        sql_rows = ""
        total_count = 0

        # Redirect rows first (amber highlight)
        if redir_sql:
            redir_method = redir_stats.get("method", "")
            redir_path = redir_stats.get("path", "")
            total_count += len(redir_sql)
            sql_rows += (
                f'<tr class="asok-redir-banner">'
                f'<td colspan="3">'
                f'&#8593; REDIRECT FROM&nbsp;'
                f'<span class="asok-method-badge">{redir_method}</span>'
                f'&nbsp;{_html.escape(redir_path)}'
                f'&nbsp;&#x2014;&nbsp;{len(redir_sql)} quer{"y" if len(redir_sql)==1 else "ies"}'
                f'</td></tr>'
            )
            for i, entry in enumerate(redir_sql):
                query = entry.get("sql", "")
                params = entry.get("params", "")
                duration = entry.get("duration", 0)
                tc = "asok-time-slow" if duration > 50 else "asok-time-warn"
                sql_rows += (
                    f'<tr class="asok-redir-row">'
                    f'<td style="color:var(--fg-3)">{i+1}</td>'
                    f'<td>'
                    f'<div class="asok-query-sql">{_html.escape(query)}</div>'
                    f'<div class="asok-query-params">Params: {_html.escape(str(params))}</div>'
                    f'</td>'
                    f'<td style="text-align:right; padding-right:24px"><span class="{tc}">{duration:.2f}ms</span></td>'
                    f'</tr>'
                )

        # Current request rows
        total_count += len(current_sql)
        for i, entry in enumerate(current_sql):
            query = entry.get("sql", "")
            params = entry.get("params", "")
            duration = entry.get("duration", 0)
            tc = "asok-time-slow" if duration > 50 else "asok-time-fast"
            sql_rows += (
                f'<tr>'
                f'<td style="color:var(--fg-3)">{i+1}</td>'
                f'<td>'
                f'<div class="asok-query-sql">{_html.escape(query)}</div>'
                f'<div class="asok-query-params">Params: {_html.escape(str(params))}</div>'
                f'</td>'
                f'<td style="text-align:right; padding-right:24px"><span class="{tc}">{duration:.2f}ms</span></td>'
                f'</tr>'
            )

        if not sql_rows:
            sql_rows = (
                '<tr><td colspan="3">'
                '<div class="asok-empty" style="padding:40px 0;">'
                '<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5"/><path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3"/></svg>'
                '<span class="asok-empty-text">No SQL queries recorded.</span>'
                '</div>'
                '</td></tr>'
            )

        # 3. Session data
        session_dict = dict(self.request.session)
        session_data = _html.escape(json.dumps(session_dict, indent=2))
        session_keys = len(session_dict)

        # 4. Request data
        req_info = {
            "URL Parameters": dict(self.request.args),
            "Body / Payload": dict(self.request.form),
        }
        try:
            if self.request.content_type == "application/json":
                req_info["JSON Payload"] = self.request.json
        except Exception:
            pass
        req_info.update({
            "Cookies": dict(self.request.cookies_dict),
            "IP": self.request.ip,
        })
        request_data = _html.escape(json.dumps(req_info, indent=2))

        # 5. Redirect info block for Request tab
        redir_info_html = ""
        if redir_stats:
            rm = redir_stats.get("method", "")
            rp = redir_stats.get("path", "")
            ra = redir_stats.get("args", {})
            rf = redir_stats.get("form", {})
            prev = {}
            if ra:
                prev["Query Parameters"] = ra
            if rf:
                prev["Body / Payload"] = rf
            redir_info_html = (
                f'<div class="asok-redir-block">'
                f'<div class="asok-redir-block-header">'
                f'&#8593; Previous Request &mdash; '
                f'<span class="asok-method-badge" style="background:rgba(217,119,6,0.3);color:var(--warn-fg);">{rm}</span>'
                f'&nbsp;{_html.escape(rp)}'
                f'</div>'
                f'<pre>{_html.escape(json.dumps(prev, indent=2))}</pre>'
                f'</div>'
            )

        # 6. Templates info
        tpl_list = getattr(self.request, "_asok_templates", [])
        blk_list = getattr(self.request, "_asok_blocks", [])
        tpl_info = {
            "Main Template": tpl_list[0] if tpl_list else "None",
            "All Templates": tpl_list,
            "Partial Blocks": blk_list,
            "WS Components": [],
        }
        tpl_json = json.dumps(tpl_info)
        tpl_data = _html.escape(json.dumps(tpl_info, indent=2))

        # 7. Load assets and assemble
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
            "[[method]]": self.request.method,
            "[[path]]": self.request.path,
            "[[tpl_data]]": tpl_data,
            "[[tpl_json]]": tpl_json,
            "[[redir_info_html]]": redir_info_html,
        }
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)

        return content

    def inject(self, html_content: str) -> str:
        """Inject the toolbar into the HTML response."""
        if "</body>" not in html_content:
            return html_content
        return html_content.replace("</body>", self.render() + "</body>")
