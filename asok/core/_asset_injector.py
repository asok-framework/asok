"""Injects framework assets (CSS/JS, nonces, directives, toolbar) into HTML.

Originally a single 145-CC monster method on AssetMixin. Split into a small
class whose entry point sequentially runs focused steps, each at A complexity.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import logging
import os
import re
from typing import Any

from ..utils.css import scope_css
from ..utils.js import scope_js
from ..utils.minify import minify_css, minify_js

logger = logging.getLogger("asok.assets")


_DIRECTIVE_MARKERS = (
    "asok-state", "asok-on:", "asok-text", "asok-show", "asok-hide",
    "asok-class:", "asok-bind:", "asok-model", "asok-if", "asok-for",
    "asok-init", "asok-ref", "asok-teleport", "asok-cloak",
    "asok-fetch", "asok-fetch-async", "asok-toggle",
    "asok-state-ref", "asok-on-ref:", "asok-text-ref", "asok-show-ref",
    "asok-hide-ref", "asok-class-ref:", "asok-bind-ref:", "asok-model-ref",
    "asok-if-ref", "asok-for-ref", "asok-init-ref", "asok-fetch-async-ref",
)

_JS_FEATURE_MARKERS = (
    "asok-state", "asok-on:", "asok-text", "asok-show", "asok-hide",
    "asok-class:", "asok-bind:", "asok-model", "asok-if", "asok-for",
)

_DATA_MARKERS = ("data-block", "data-sse", "data-url", "data-method")

_WIDGET_MARKERS = (
    "Asok.", "asok-dropdown", "asok-table", "asok-toggle",
    "asok-badge", "asok-pagination",
)

_DIRECTIVE_EXPR_ATTRS = frozenset({
    "asok-text", "asok-html", "asok-show", "asok-hide",
    "asok-if", "asok-elif", "asok-state", "asok-init", "asok-fetch-async",
})

_DIRECTIVE_PREFIXES = ("asok-on:", "asok-class:", "asok-bind:")


def precompile_directives(app, html: str) -> tuple[str, dict[str, str]]:
    """Compile asok-* directive attributes in *html* to hashed refs.

    We temporarily mask the content of ``<code>``, ``<pre>``, and
    ``<script>`` elements so that directive-like patterns inside
    documentation code examples (e.g. ``<code>asok-class:x="..."</code>``)
    are never mistaken for real, live directive attributes.
    """
    registry: dict[str, str] = {}
    _masks: list[str] = []

    # 1. Mask non-directive content so the regex below cannot match inside it.
    _MASK_RE = re.compile(
        r'<(code|pre|script)(\s[^>]*)?>.*?</\1>',
        re.DOTALL | re.IGNORECASE,
    )

    def _mask(m: re.Match) -> str:
        idx = len(_masks)
        _masks.append(m.group(0))
        return f'\x00ASOK_MASK_{idx}\x00'

    masked_html = _MASK_RE.sub(_mask, html)

    # 2. Compile directives on the masked HTML.
    def replacer(match):
        return _replace_directive(app, registry, match)

    processed = re.sub(
        r'(?<![a-zA-Z0-9-])(asok-[a-zA-Z0-9:.\-]+)=([\'"])(.*?)\2',
        replacer, masked_html, flags=re.DOTALL,
    )

    # 3. Restore masked blocks.
    for i, original in enumerate(_masks):
        processed = processed.replace(f'\x00ASOK_MASK_{i}\x00', original)

    return processed, registry


def _expr_hash(expr: str) -> str:
    return hashlib.md5(expr.strip().encode()).hexdigest()[:12]


def _replace_directive(app, registry: dict[str, str], match) -> str:
    name = match.group(1)
    val = _html.unescape(match.group(3))
    if name.endswith("-ref"):
        return match.group(0)
    if name == "asok-for":
        return _replace_for_directive(app, registry, name, val, match)
    if _is_expr_attribute(name):
        return _replace_expr_directive(app, registry, name, val)
    return match.group(0)


def _replace_for_directive(app, registry, name, val, match) -> str:
    if " in " not in val:
        return match.group(0)
    var_part, expr_part = val.split(" in ", 1)
    _assert_safe_expression(app, name, expr_part)
    h = _expr_hash(expr_part)
    registry[h] = expr_part
    return f'asok-for-ref="{h}" asok-for-var="{var_part.strip()}"'


def _replace_expr_directive(app, registry, name, val) -> str:
    _assert_safe_expression(app, name, val)
    h = _expr_hash(val)
    registry[h] = val
    if ":" in name:
        parts = name.split(":", 1)
        return f'{parts[0]}-ref:{parts[1]}="{h}"'
    return f'{name}-ref="{h}"'


def _is_expr_attribute(name: str) -> bool:
    if name in _DIRECTIVE_EXPR_ATTRS:
        return True
    if name == "asok-class":
        return True
    return any(name.startswith(p) for p in _DIRECTIVE_PREFIXES)


def _assert_safe_expression(app, name: str, val: str) -> None:
    if app._validate_directive_expression(val):
        return
    raise ValueError(
        f"SECURITY: Unsafe expression in {name}: '{val}'. "
        "Only safe Python expressions are allowed in directives. "
        "Forbidden: eval(), exec(), __import__(), dunder methods, etc."
    )


class AssetInjector:
    """Wraps a single _inject_assets invocation in a stateful, testable object."""

    def __init__(
        self, app: Any, request: Any, content: str, nonce: str,
        stream: bool, include_scripts: bool, only_scripts: bool,
    ) -> None:
        self.app = app
        self.request = request
        self.content = content
        self.nonce = nonce
        self.stream = stream
        self.include_scripts = include_scripts
        self.only_scripts = only_scripts
        self.registry: dict[str, str] = {}
        self.is_block = bool(request.environ.get("HTTP_X_BLOCK"))

    def inject(self) -> str:
        self._ensure_pending_buffers()
        self._inject_meta()
        self._inject_page_id_and_assets()
        self._flush_styles_to_head()
        self._inject_csrf_meta()
        self._inject_security_utils()
        self._inject_transitions()
        self._inject_reactive()
        self._inject_alive()
        self._inject_nonce_in_existing_tags()
        self._inject_directives()
        self._inject_widgets()
        self._inject_reload()
        self._flush_pending_styles()
        self._flush_pending_scripts()
        self._inject_toolbar()
        return self.content

    # ── state helpers ───────────────────────────────────────────

    def _ensure_pending_buffers(self) -> None:
        request = self.request
        if not self.nonce or len(self.nonce) < 10:
            self.nonce = request.nonce
        request._nonce = self.nonce
        if not hasattr(request, "_asok_pending_scripts"):
            request._asok_pending_scripts = ""
        if not hasattr(request, "_asok_pending_styles"):
            request._asok_pending_styles = ""

    # ── 1. meta tags ────────────────────────────────────────────

    def _inject_meta(self) -> None:
        if self.only_scripts or getattr(self.request, "_asok_meta_done", False):
            return
        meta_obj = getattr(self.request, "meta", None)
        if not meta_obj:
            return
        meta_html = self._build_meta_html(meta_obj)
        if not meta_html:
            return
        self.request._asok_meta_done = True
        self._inject_into_head(meta_html)

    def _build_meta_html(self, meta_obj: Any) -> str:
        meta_html = ""
        if meta_obj._title:
            self._strip_existing_title()
            meta_html += f"    <title>{_html.escape(str(meta_obj._title))}</title>\n"
        if meta_obj._description:
            self._strip_existing_description()
            meta_html += (
                f'    <meta name="description" content="{_html.escape(str(meta_obj._description))}">\n'
            )
        for item in meta_obj._items:
            meta_html += self._render_meta_item(item, meta_obj)
        return meta_html

    def _strip_existing_title(self) -> None:
        lower = self.content.lower()
        start = lower.find("<title>")
        if start == -1:
            return
        end = lower.find("</title>", start)
        if end != -1:
            self.content = self.content[:start] + self.content[end + 8 :]

    def _strip_existing_description(self) -> None:
        self.content = re.sub(
            r'<meta\s+name=["\']description["\']\s+content=["\'].*?["\']\s*/?>',
            "", self.content, flags=re.IGNORECASE,
        )

    @classmethod
    def _render_meta_item(cls, item, meta_obj) -> str:
        itype, ikey, ival, ikwargs = item
        if itype == "name":
            return cls._render_meta_name(ikey, ival, meta_obj)
        if itype == "property":
            return f'    <meta property="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">\n'
        if itype == "link":
            return cls._render_meta_link(ikey, ival, ikwargs)
        return ""

    @staticmethod
    def _render_meta_name(ikey, ival, meta_obj) -> str:
        if ikey.lower() == "description" and meta_obj._description:
            return ""
        return f'    <meta name="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">\n'

    @staticmethod
    def _render_meta_link(ikey, ival, ikwargs) -> str:
        extra = " ".join(
            f'{k}="{_html.escape(str(v))}"' for k, v in ikwargs.items()
        )
        return f'    <link rel="{_html.escape(ikey)}" href="{_html.escape(ival)}" {extra}>\n'

    def _inject_into_head(self, html_chunk: str) -> None:
        if "<head>" in self.content:
            self.content = self.content.replace("<head>", "<head>\n" + html_chunk, 1)
            return
        if "<head " not in self.content:
            return
        idx = self.content.find("<head ")
        end = self.content.find(">", idx)
        if end != -1:
            self.content = (
                self.content[: end + 1] + "\n" + html_chunk + self.content[end + 1 :]
            )

    # ── 2. page_id + scoped CSS/JS ─────────────────────────────

    def _inject_page_id_and_assets(self) -> None:
        if not self.request.page_id:
            return
        self._inject_page_id_attribute()
        self._inject_scoped_css()
        self._inject_scoped_js()

    def _inject_page_id_attribute(self) -> None:
        if getattr(self.request, "_asok_page_id_done", False):
            return
        page_id = self.request.page_id
        if "<body" in self.content:
            self._set_body_page_id(page_id)
        if self.stream:
            self._inject_page_id_marker(page_id)

    def _set_body_page_id(self, page_id: str) -> None:
        if 'data-page-id="' not in self.content:
            self.content = self.content.replace(
                "<body", f'<body data-page-id="{page_id}"', 1
            )
            return
        self.content = re.sub(
            r'data-page-id="[^"]*"', f'data-page-id="{page_id}"', self.content, 1
        )

    def _inject_page_id_marker(self, page_id: str) -> None:
        marker = f"<!-- page-id:{page_id} -->\n"
        if "</body>" in self.content.lower():
            self.content = re.sub(
                r"(</body>)", lambda m: marker + m.group(1),
                self.content, flags=re.I, count=1,
            )
        else:
            self.content += marker
        self.request._asok_page_id_done = True

    def _inject_scoped_css(self) -> None:
        request = self.request
        if getattr(request, "_asok_css_done", False):
            return
        css_path = request.scoped_assets.get("css")
        if not css_path:
            return
        try:
            self._do_inject_scoped_css(css_path)
        except Exception:
            pass

    def _do_inject_scoped_css(self, css_path: str) -> None:
        with open(css_path, "r", encoding="utf-8") as f:
            raw_css = f.read()
        page_id = self.request.page_id
        scoped = scope_css(raw_css, page_id)
        if not self.app.config.get("DEBUG") and not self.app.config.get("ASOK_BUILD"):
            scoped = minify_css(scoped)
        safe_page_id = _html.escape(page_id, quote=True)
        safe_css = scoped.replace("</style>", "<\\/style>")
        style_tag = (
            f'\n<style id="asok-scoped-css" data-page-id="{safe_page_id}">\n'
            f"{safe_css}\n</style>\n"
        )
        self._inject_style_into_head(style_tag)
        self.request._asok_css_done = True

    def _inject_style_into_head(self, style_tag: str) -> None:
        if "</head>" in self.content.lower():
            self.content = re.sub(
                r"(</head>)", lambda m: style_tag + m.group(1),
                self.content, flags=re.I, count=1,
            )
        else:
            self.content = style_tag + self.content

    def _inject_scoped_js(self) -> None:
        request = self.request
        if getattr(request, "_asok_js_done", False):
            return
        js_path = request.scoped_assets.get("js")
        if not js_path:
            return
        try:
            self._do_inject_scoped_js(js_path)
        except Exception:
            pass

    def _do_inject_scoped_js(self, js_path: str) -> None:
        with open(js_path, "r", encoding="utf-8") as f:
            raw_js = f.read()
        scoped = scope_js(raw_js)
        if not self.app.config.get("DEBUG") and not self.app.config.get("ASOK_BUILD"):
            scoped = minify_js(scoped)
        safe_js = scoped.replace("</script>", "<\\/script>")
        nonce = self.nonce
        self.request._asok_pending_scripts += (
            f'\n<script id="asok-scoped-js" nonce="{nonce}">'
            "(function(){"
            "const init=function(){" + safe_js + "};"
            "if(document.readyState==='loading')"
            "document.addEventListener('DOMContentLoaded',init);"
            "else init();"
            "})()</script>\n"
        )
        self.request._asok_js_done = True

    # ── 3. flush pending styles before CSRF ─────────────────────

    def _flush_styles_to_head(self) -> None:
        request = self.request
        styles = request._asok_pending_styles
        if not styles or getattr(request, "_asok_styles_done", False):
            return
        if "</head>" in self.content.lower():
            request._asok_styles_done = True
            request._asok_pending_styles = ""
            self.content = re.sub(
                r"(</head>)", lambda m: styles + m.group(1),
                self.content, flags=re.I, count=1,
            )
        elif not self.stream:
            request._asok_styles_done = True
            request._asok_pending_styles = ""
            self.content = styles + self.content

    # ── 4. CSRF meta ─────────────────────────────────────────────

    def _inject_csrf_meta(self) -> None:
        if self.only_scripts or getattr(self.request, "_asok_csrf_done", False):
            return
        token = getattr(self.request, "csrf_token_value", "")
        csrf_meta = f'<meta name="csrf-token" content="{token}">'
        if "<head>" not in self.content.lower():
            return
        self.content = re.sub(
            r"(<head.*?>)", lambda m: m.group(1) + "\n" + csrf_meta,
            self.content, flags=re.I, count=1,
        )
        self.request._asok_csrf_done = True

    # ── 5. security utils JS ────────────────────────────────────

    def _inject_security_utils(self) -> None:
        if not self._needs_security_utils():
            return
        self.request._asok_security_utils_done = True
        js = self.app.get_asset("asok_security_utils.min.js")
        self.request._asok_pending_scripts += (
            f'<script nonce="{self.nonce}">\n{js}\n</script>\n'
        )

    def _is_admin_request(self) -> bool:
        admin = getattr(self.app, "_admin", None)
        if admin:
            return self.request.path == admin.prefix or self.request.path.startswith(admin.prefix + "/")
        return self.request.path.startswith("/admin")

    def _needs_security_utils(self) -> bool:
        if self.is_block or getattr(self.request, "_asok_security_utils_done", False):
            return False
        return self._content_uses_js_features()

    _JS_TRIGGER_SUBSTRINGS = ("asok-transition", "data-asok-component", "ws-")

    def _content_uses_js_features(self) -> bool:
        content = self.content
        if any(s in content for s in self._JS_TRIGGER_SUBSTRINGS):
            return True
        return self._content_uses_data_markers() or self._content_uses_directive_markers()

    def _content_uses_directive_markers(self) -> bool:
        return any(attr in self.content for attr in _JS_FEATURE_MARKERS)

    # ── 6. transitions ─────────────────────────────────────────

    def _inject_transitions(self) -> None:
        if not self._needs_transitions():
            return
        self.request._asok_transition_done = True
        nonce = self.nonce
        css = self.app.get_asset("asok_transitions.min.css")
        js = self.app.get_asset("asok_transitions.min.js")
        self.request._asok_pending_styles += (
            f'<style id="asok-transitions" nonce="{nonce}">{css}</style>\n'
        )
        self.request._asok_pending_scripts += (
            f'<script id="asok-transition-engine" nonce="{nonce}">{js}</script>\n'
        )

    def _needs_transitions(self) -> bool:
        if self.is_block or getattr(self.request, "_asok_transition_done", False):
            return False
        if "asok-transition" in self.content:
            return True
        return self.stream and self.only_scripts

    # ── 7. reactive SPA ─────────────────────────────────────────

    def _inject_reactive(self) -> None:
        if not self._needs_reactive():
            return
        self.request._asok_reactive_done = True
        spa_js = self.app.get_asset("asok_spa.min.js")
        self.request._asok_pending_scripts += (
            f'<script nonce="{self.nonce}">\n{spa_js}\n</script>'
        )

    def _is_reactive_blocked(self) -> bool:
        if self._is_admin_request():
            return True
        return bool(self.is_block or getattr(self.request, "_asok_reactive_done", False))

    def _needs_reactive(self) -> bool:
        if self._is_reactive_blocked():
            return False
        return self._content_uses_data_markers() or (self.stream and self.only_scripts)

    def _content_uses_data_markers(self) -> bool:
        return any(attr in self.content for attr in _DATA_MARKERS)

    # ── 8. alive (WebSocket components) ────────────────────────

    def _inject_alive(self) -> None:
        if not self._needs_alive():
            return
        self.request._asok_alive_done = True
        nonce = self.nonce
        ws_port = self.app.config.get("WS_PORT", 8001)
        alive_js = self.app.get_asset("asok_alive.min.js")
        self.request._asok_pending_scripts += (
            f'<script nonce="{nonce}">window.ASOK_WS_PORT = {ws_port};</script>\n'
            f'<script nonce="{nonce}">\n{alive_js}\n</script>\n'
        )

    def _needs_alive(self) -> bool:
        if self.is_block or getattr(self.request, "_asok_alive_done", False):
            return False
        return self._content_uses_components() or (self.stream and self.only_scripts)

    def _content_uses_components(self) -> bool:
        return "data-asok-component" in self.content or "ws-" in self.content

    # ── 9. inject nonce into existing script/style/link ────────

    def _inject_nonce_in_existing_tags(self) -> None:
        nonce = self.nonce

        def inject_nonce_attr(m):
            attrs = m.group(2)
            if 'nonce="' in attrs.lower():
                return re.sub(r'(?i)nonce=".*?"', f'nonce="{nonce}"', m.group(0))
            return f"<{m.group(1)}{attrs} nonce=\"{nonce}\">"

        self.content = re.sub(
            r"<(script|style|link)\b([^>]*?)>",
            inject_nonce_attr, self.content, flags=re.IGNORECASE,
        )

    # ── 10. directives precompilation + runtime ────────────────

    def _inject_directives(self) -> None:
        if not self._needs_directives():
            return
        if self._has_precompiled_registry_file():
            self._inject_precompiled_directives()
        else:
            self._inject_runtime_directives()

    def _needs_directives(self) -> bool:
        if any(marker in self.content for marker in _DIRECTIVE_MARKERS):
            return True
        return getattr(self.request, "_asok_needs_directives", False)

    def _has_precompiled_registry_file(self) -> bool:
        debug = self.app.config.get("DEBUG", False)
        if debug:
            return False
        registry_file = self._registry_file_path()
        return os.path.exists(registry_file)

    def _registry_file_path(self) -> str:
        return os.path.join(self.app._partials_path, "js", "directives_registry.js")

    def _inject_precompiled_directives(self) -> None:
        if getattr(self.request, "_asok_directives_done", False) or self.is_block:
            return
        self.request._asok_directives_done = True
        nonce = self.nonce
        css = self.app.get_asset("asok_directives.min.css")
        self.request._asok_pending_styles += f'<style nonce="{nonce}">{css}</style>'
        registry_url = self._versioned_registry_url()
        js = self.app.get_asset("asok_directives.min.js")
        self.request._asok_pending_scripts += (
            f'<script nonce="{nonce}">\n'
            f'window.Asok = window.Asok || {{}}; window.Asok.nonce = "{nonce}";\n'
            f"</script>\n"
            f'<script src="{registry_url}" nonce="{nonce}"></script>\n'
            f'<script nonce="{nonce}">\n{js}\n</script>'
        )

    def _versioned_registry_url(self) -> str:
        registry_url = "/js/directives_registry.js"
        h = self.app._static_hash("js/directives_registry.js")
        if h:
            registry_url += f"?v={h}"
        return registry_url

    def _inject_runtime_directives(self) -> None:
        self.content, self.registry = precompile_directives(self.app, self.content)
        registry_js = self._build_registry_js()
        if getattr(self.request, "_asok_directives_done", False) or self.is_block:
            if registry_js:
                self.request._asok_pending_scripts += (
                    f'<script nonce="{self.nonce}">\n{registry_js}</script>\n'
                )
            return
        self._inject_directives_full(registry_js)

    def _build_registry_js(self) -> str:
        if not self.registry:
            return ""
        entries = [self._registry_entry(h, expr) for h, expr in self.registry.items()]
        return (
            "window.__asok_registry = Object.assign(window.__asok_registry || {}, {\n"
            + ",\n".join(entries) + "\n});\n"
        )

    def _registry_entry(self, h: str, expr: str) -> str:
        is_stmt = self._expression_is_statement(expr)
        if expr.strip().startswith("{") and not is_stmt:
            expr = f"({expr})"
        body = f"return ({expr})" if not is_stmt else expr
        body = re.sub(r"\s+", " ", body).strip()
        is_async = self.app._is_async_expression_cached(expr)
        fn_prefix = "async " if is_async else ""
        return (
            f"    {json.dumps(h)}: {fn_prefix}function($, $store, $el, $event, $refs, $nextTick)"
            f" {{ with($||{{}}) {{ {body} }} }}"
        )

    @staticmethod
    def _expression_is_statement(expr: str) -> bool:
        if ";" in expr or "return " in expr:
            return True
        return bool(re.search(r"\b(if|for|while|const|let|var|function)\b", expr))

    def _inject_directives_full(self, registry_js: str) -> None:
        self.request._asok_directives_done = True
        nonce = self.nonce
        css = self.app.get_asset("asok_directives.min.css")
        js = self.app.get_asset("asok_directives.min.js")
        self.request._asok_pending_styles += f'<style nonce="{nonce}">{css}</style>'
        self.request._asok_pending_scripts += (
            f'<script nonce="{nonce}">\n'
            f'window.Asok = window.Asok || {{}}; window.Asok.nonce = "{nonce}";\n'
            f"{registry_js}\n{js}\n</script>"
        )

    # ── 11. widgets ─────────────────────────────────────────────

    def _inject_widgets(self) -> None:
        if not self._needs_widgets():
            return
        self.request._asok_widgets_done = True
        self._append_widget_assets()

    def _needs_widgets(self) -> bool:
        if getattr(self.request, "_asok_widgets_done", False):
            return False
        if self._content_uses_widgets():
            return True
        if self._has_precompiled_registry_file():
            return self._precompiled_uses_widgets()
        return self._registry_uses_widgets()

    def _content_uses_widgets(self) -> bool:
        return any(marker in self.content for marker in _WIDGET_MARKERS)

    def _registry_uses_widgets(self) -> bool:
        return any(
            any(marker in val for marker in _WIDGET_MARKERS)
            for val in self.registry.values()
        )

    def _precompiled_uses_widgets(self) -> bool:
        if hasattr(self.app, "_precompiled_uses_widgets"):
            return self.app._precompiled_uses_widgets
        self.app._precompiled_uses_widgets = self._detect_widgets_in_registry_file()
        return self.app._precompiled_uses_widgets

    def _detect_widgets_in_registry_file(self) -> bool:
        registry_file = self._registry_file_path()
        try:
            if not os.path.exists(registry_file):
                return False
            with open(registry_file, "r", encoding="utf-8") as f:
                content = f.read()
            return any(marker in content for marker in _WIDGET_MARKERS)
        except Exception:
            return False

    def _append_widget_assets(self) -> None:
        nonce = self.nonce
        try:
            js = self.app.get_asset("asok_widgets.min.js")
            self.request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n{js}\n</script>\n'
            )
        except Exception:
            pass
        try:
            css = self.app.get_asset("asok_widgets.min.css")
            self.request._asok_pending_styles += (
                f'<style nonce="{nonce}">{css}</style>\n'
            )
        except Exception:
            pass

    # ── 12. live-reload (DEBUG only) ────────────────────────────

    def _inject_reload(self) -> None:
        if not self._needs_reload():
            return
        self.request._asok_reload_done = True
        reload_js = self.app.get_asset("asok_reload.min.js")
        self.request._asok_pending_scripts += (
            f'<script nonce="{self.nonce}">{reload_js}</script>'
        )

    def _needs_reload(self) -> bool:
        if not self.app.config.get("DEBUG"):
            return False
        if self.is_block:
            return False
        return not getattr(self.request, "_asok_reload_done", False)

    # ── 13. flush pending styles (final) ───────────────────────

    def _flush_pending_styles(self) -> None:
        if self.is_block:
            return
        styles = self.request._asok_pending_styles
        if not styles:
            return
        if "</head>" in self.content.lower():
            self.request._asok_pending_styles = ""
            self.content = re.sub(
                r"(</head>)", lambda m: styles + m.group(1),
                self.content, flags=re.I, count=1,
            )
        elif not self.stream:
            self.request._asok_pending_styles = ""
            self.content = styles + self.content

    # ── 14. flush pending scripts (final) ──────────────────────

    def _flush_pending_scripts(self) -> None:
        scripts = self.request._asok_pending_scripts
        if not self._should_flush_scripts(scripts):
            return
        if "</body>" in self.content.lower():
            self._inject_scripts_before_body_close(scripts)
        elif not self.stream or self.is_block:
            self._inject_scripts_no_body(scripts)

    def _should_flush_scripts(self, scripts: str) -> bool:
        if not scripts:
            return False
        return not getattr(self.request, "_asok_scripts_done", False)

    def _inject_scripts_before_body_close(self, scripts: str) -> None:
        self.request._asok_scripts_done = True
        self.request._asok_pending_scripts = ""
        self.content = re.sub(
            r"(</body>)", lambda m: scripts + m.group(1),
            self.content, flags=re.I, count=1,
        )

    def _inject_scripts_no_body(self, scripts: str) -> None:
        lower = self.content.lower()
        if "</html>" in lower or "</template>" in lower:
            self.request._asok_scripts_done = True
            self.request._asok_pending_scripts = ""
            self.content = self.content + "\n" + scripts
            return
        self._inject_scripts_in_block(scripts)

    def _inject_scripts_in_block(self, scripts: str) -> None:
        stripped = self.content.strip()
        inside_tag = re.search(r"<[^>]*$", self.content)
        is_continuation = stripped and not stripped.startswith("<") and ">" in stripped
        self.request._asok_scripts_done = True
        self.request._asok_pending_scripts = ""
        if inside_tag or is_continuation:
            self.content = scripts + self.content
        else:
            self.content = self.content + "\n" + scripts

    # ── 15. developer toolbar ──────────────────────────────────

    def _inject_toolbar(self) -> None:
        if self.is_block or not self._show_toolbar():
            return
        lower = self.content.lower()
        if "</html>" not in lower and "</body>" not in lower:
            return
        self._try_inject_toolbar()

    def _try_inject_toolbar(self) -> None:
        try:
            from ..toolbar import DeveloperToolbar

            toolbar = DeveloperToolbar(self.request, self.app)
            self.content = toolbar.inject(self.content)
        except ImportError as e:
            logger.debug(f"Toolbar import failed: {e}")
        except Exception as e:
            logger.error(f"Toolbar injection failed: {e}", exc_info=True)

    def _show_toolbar(self) -> bool:
        config = self.app.config
        if "TOOLBAR" in config:
            return self._coerce_bool(config.get("TOOLBAR"))
        return self._coerce_bool(config.get("DEBUG"))

    @staticmethod
    def _coerce_bool(val: Any) -> bool:
        if isinstance(val, str):
            return val.lower() in ("true", "yes", "1", "on")
        return bool(val)
