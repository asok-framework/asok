from __future__ import annotations

import html
import os
import time
from typing import Any, Iterator, Optional, Type, TypeVar, Union

from asok.templates import (
    SafeString,
    render_block_string,
    render_template_string,
    stream_template_string,
)
from asok.utils.image import is_image

# Generic type for shared variable resolution
T = TypeVar("T")


class TemplateMixin:
    """Mixin for template rendering, context management and static assets on Request."""

    def _resolve_template(self: Any, filepath: str) -> tuple[str, str]:
        """Convert a template path to its content and identify the partials root."""
        root = self.environ.get("asok.root", os.getcwd())
        if (
            not os.path.isabs(filepath)
            and not filepath.startswith("src/")
            and hasattr(self, "_current_page_file")
            and self._current_page_file
        ):
            page_dir = os.path.dirname(self._current_page_file)
            path = os.path.join(page_dir, filepath)
        else:
            path = os.path.join(root, filepath)

        # Automatic extension resolution if file not found
        if not os.path.isfile(path):
            # 1. Try appending extensions (for request.html('page'))
            for ext in (".html", ".asok"):
                if os.path.isfile(path + ext):
                    path = path + ext
                    break

            # 2. Try swapping extensions if still not found (for request.html('page.html') -> page.asok)
            if not os.path.isfile(path):
                base_path, current_ext = os.path.splitext(path)
                if current_ext == ".html" and os.path.isfile(base_path + ".asok"):
                    path = base_path + ".asok"
                elif current_ext == ".asok" and os.path.isfile(base_path + ".html"):
                    path = base_path + ".html"

        # CONVENTION: Enforce strict naming convention for page templates
        # Only allow 'page.html' or 'page.asok' as template names
        # Exception: partials (src/partials/), layouts (src/html/), and components can have any name
        normalized_path = path.replace("\\", "/")
        is_partial = (
            "/partials/" in normalized_path
            or "/html/" in normalized_path
            or "/components/" in normalized_path
        )

        if not is_partial:
            basename = os.path.basename(path)
            if basename not in ("page.html", "page.asok"):
                raise ValueError(
                    f"Invalid template name: '{basename}'. "
                    f"Page templates must be named 'page.html' or 'page.asok'. "
                    f"This convention ensures code readability and consistency.\n"
                    f"  ✓ Valid: src/pages/contact/page.html\n"
                    f"  ✗ Invalid: src/pages/contact/contact_form.html\n"
                    f"Note: Partials (src/partials/), layouts (src/html/), and components can have any name."
                )

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Proactively scan template content for directives to enable the JS engine
        if any(
            attr in content
            for attr in [
                "asok-state",
                "asok-on:",
                "asok-text",
                "asok-show",
                "asok-hide",
                "asok-class:",
                "asok-bind:",
                "asok-model",
                "asok-if",
                "asok-for",
                "asok-init",
                "asok-ref",
                "asok-teleport",
                "asok-cloak",
                "asok-fetch",
                "asok-fetch-async",
            ]
        ):
            self._asok_needs_directives = True

        tpl_root = os.path.join(root, "src/partials")
        return content, tpl_root

    def share(self: Any, name: str, value: Any = None) -> Union[str, Any]:
        """Set or get a shared variable bound to this request.

        If a value is provided, it is stored in the per-request cache.
        If no value is provided, it returns the shared value.
        """
        if value is not None:
            self.__dict__.setdefault("_shared_cache", {})[name] = value
            return ""
        return self.shared(name)

    def shared(
        self: Any, name: str, expected_type: Optional[Type[T]] = None
    ) -> Union[T, Any]:
        """Get a shared variable bound to this request (cached per request).

        Variables registered via app.share() or request.share() are auto-resolved.
        Callables are invoked with the current request, and Form templates are bound.
        Providing an 'expected_type' enables IDE autocompletion for the returned object.
        """
        cache = self.__dict__.setdefault("_shared_cache", {})

        # 1. Try to find the value (already cached or in app defaults)
        val = cache.get(name)
        was_in_cache = name in cache

        if val is None and not was_in_cache:
            app_ref: Optional[Any] = self.environ.get("asok.app")
            if not app_ref or name not in getattr(app_ref, "_shared", {}):
                return None
            val = app_ref._shared[name]

        # 2. Resolution logic
        from asok.forms import Form

        resolved = val
        if isinstance(val, Form) and getattr(val, "_is_template", False):
            resolved = val._bind(self)
        elif not isinstance(val, Form) and callable(val):
            # Resolve callables (functions or Form classes)
            resolved = val(self)

        # 3. Always update cache with the resolved version to ensure stability
        if resolved is not val or not was_in_cache:
            cache[name] = resolved

        return resolved

    def shared_form(self: Any, name: str) -> Any:
        """Typed internal helper to get a shared form with full IDE autocompletion.

        Returns an instance of Form automatically bound to this request.
        """
        from asok.forms import Form

        return self.shared(name, Form)

    def _template_context(self: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Assemble the standard context variables for template rendering."""
        ctx = {
            "request": self,
            "nonce": getattr(self, "nonce", ""),
            "__": self.__,
            "static": self.static,
            "get_flashed_messages": self.get_flashed_messages,
            "meta": self.meta,
        }

        # -- component() helper ----------------------------------------
        # Instance counter for stable CIDs, shared across all templates in this request
        if not hasattr(self, "_comp_counters"):
            self._comp_counters = {}

        def component(name, *args, **kwargs):
            """Instantiate and render a reactive component."""
            from asok.component import COMPONENTS_REGISTRY

            cls = COMPONENTS_REGISTRY.get(name)
            if cls is None:
                return SafeString(f"<!-- Component '{name}' not found -->")

            # Stable CID: explicit or auto (tpl--name--index)
            cid = kwargs.pop("cid", None)
            if cid is None:
                tpl_name = getattr(self, "_current_page_file", "global")
                tpl_name = os.path.basename(tpl_name).replace(".", "-")
                self._comp_counters[name] = self._comp_counters.get(name, 0) + 1
                cid = f"{tpl_name}--{name.lower()}--{self._comp_counters[name]}"

            app_ref = self.environ.get("asok.app")
            secret = (
                app_ref.config.get("SECRET_KEY") if app_ref else os.getenv("SECRET_KEY")
            )
            if not secret:
                raise RuntimeError(
                    "SECRET_KEY is not configured. This should never happen if Asok() is properly initialized."
                )

            # Try to restore from session (persists across page refreshes)
            sess = self.session
            saved_signed = sess.get(f"_comp_{cid}")

            slot = kwargs.pop("slot", None)
            if slot is not None:
                slot = SafeString(slot)

            if saved_signed:
                try:
                    instance = cls._from_signed_state(saved_signed, secret, cid=cid)
                    if instance is not None:
                        instance._slot = slot
                        return SafeString(str(instance))
                except Exception:
                    pass

            # Fresh instance
            if args:
                state_keys = [
                    k
                    for k in cls.__dict__
                    if not k.startswith("_") and not callable(cls.__dict__[k])
                ]
                for i, val in enumerate(args):
                    if i < len(state_keys):
                        kwargs.setdefault(state_keys[i], val)

            instance = cls(_cid=cid, **kwargs)
            instance._slot = slot
            # Save initial state so it survives a refresh
            signed_state = instance._sign_state(secret)
            sess[f"_comp_{cid}"] = signed_state
            return SafeString(str(instance))

        ctx["component"] = component

        # Inject all app.share()d variables, bound per-request and cached
        app_ref = self.environ.get("asok.app")
        if app_ref and getattr(app_ref, "_shared", None):
            for name in app_ref._shared:
                try:
                    ctx[name] = self.shared(name)
                except Exception:
                    pass

        # Inject per-request shared variables (manual share)
        cache = self.__dict__.get("_shared_cache", {})
        ctx.update(cache)

        ctx.update(context)
        return ctx

    def html(self: Any, filepath: str, **context: Any) -> str:
        """Render an HTML template and return the result as a string."""
        # Auto-detect block request (from data-block JS swap)
        block_header = self.environ.get("HTTP_X_BLOCK")
        if block_header:
            names = [b.strip() for b in block_header.split(",")]
            for name in names:
                if name.startswith("#"):
                    raise ValueError(
                        f"Invalid block name '{name}'. Block names should not include the '#' prefix. "
                        f"Check your 'data-block' attributes in templates."
                    )
            if len(names) == 1:
                return self.block(filepath, names[0], **context)
            parts = []
            for name in names:
                content = self.block(filepath, name, **context)
                parts.append(f'<template data-block="{name}">{content}</template>')
            return "".join(parts)

        content, tpl_root = self._resolve_template(filepath)
        if not hasattr(self, "_asok_templates"):
            self._asok_templates = []
        self._asok_templates.append(filepath)
        return render_template_string(
            content,
            self._template_context(context),
            root_dir=tpl_root,
            inject_block_markers=True,  # Inject markers for data-block targeting
        )

    def stream(self: Any, filepath: str, **context: Any) -> Any:
        """Native HTML streaming response using generators."""
        # Detect block request
        block_header = self.environ.get("HTTP_X_BLOCK")
        if block_header:
            return self._stream_blocks(filepath, block_header, **context)

        content, tpl_root = self._resolve_template(filepath)
        return stream_template_string(
            content,
            self._template_context(context),
            root_dir=tpl_root,
            inject_block_markers=True,  # Inject markers for data-block targeting
        )

    def _stream_blocks(
        self: Any, filepath: str, block_header: str, **context: Any
    ) -> Iterator[str]:
        """Internal helper to stream multiple template blocks.

        Wraps content in <template> tags ONLY for SPA block requests.
        For normal page loads, returns unwrapped content for better SEO and first paint.
        """
        names = [b.strip() for b in block_header.split(",")]
        for name in names:
            if not name:
                continue
            if name.startswith("#"):
                raise ValueError(
                    f"Invalid block name '{name}'. Use the block name directly without the '#' prefix."
                )
        content, tpl_root = self._resolve_template(filepath)

        # Check if this is a genuine SPA block request (has X-Block header)
        # vs a streaming response that happens to have multiple blocks
        is_spa_request = bool(self.environ.get("HTTP_X_BLOCK"))

        for name in names:
            self._current_block = name
            tpl_ctx = self._template_context(context)

            block_html = render_block_string(content, name, tpl_ctx, root_dir=tpl_root)

            # Only wrap in <template> for actual SPA block requests
            if is_spa_request:
                yield f'<template data-block="{name}">{block_html}</template>'
            else:
                # For normal page loads, return unwrapped content
                # This ensures better SEO, accessibility, and first paint
                yield block_html

        # CRITICAL: Include scoped CSS/JS in SPA block responses
        # This ensures that page-specific styles and scripts are preserved during navigation
        if is_spa_request and hasattr(self, "scoped_assets"):
            from asok.utils.css import scope_css
            from asok.utils.minify import minify_css, minify_js

            page_id = getattr(self, "page_id", None)

            # Include scoped CSS if it exists
            if self.scoped_assets.get("css") and page_id:
                try:
                    with open(self.scoped_assets["css"], "r", encoding="utf-8") as f:
                        raw_css = f.read()
                    scoped_css = scope_css(raw_css, page_id)
                    # Minify in production
                    if not self.environ.get("DEBUG"):
                        scoped_css = minify_css(scoped_css)
                    # SECURITY: Escape page_id for safe HTML attribute injection
                    # Prevent CSS from breaking </style> tag by replacing it

                    safe_page_id = html.escape(page_id, quote=True)
                    safe_css = scoped_css.replace("</style>", "<\\/style>")
                    yield f'<style id="asok-scoped-css" data-page-id="{safe_page_id}">{safe_css}</style>'
                except Exception:
                    pass  # Silently fail if CSS can't be loaded

            # Include scoped JS if it exists
            if self.scoped_assets.get("js"):
                try:
                    with open(self.scoped_assets["js"], "r", encoding="utf-8") as f:
                        raw_js = f.read()
                    from asok.utils.js import scope_js

                    scoped_js = scope_js(raw_js, page_id) if page_id else raw_js
                    # Minify in production
                    if not self.environ.get("DEBUG"):
                        scoped_js = minify_js(scoped_js)
                    nonce = getattr(self, "nonce", "")
                    # SECURITY: Prevent JS from breaking </script> tag
                    # Replace </script> with <\/script> to avoid premature tag closure
                    safe_js = scoped_js.replace("</script>", "<\\/script>")
                    yield f"<script id=\"asok-scoped-js\" nonce=\"{nonce}\">(function(){{const init=function(){{{safe_js}}};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();}})();</script>"
                except Exception:
                    pass  # Silently fail if JS can't be loaded

    def block(
        self: Any, filepath: str, block_name: Optional[str] = None, **context: Any
    ) -> str:
        """Render a specific block from an HTML template.

        Returns the block content for data-block updates. The JavaScript will use
        HTML comment markers to locate and replace the content.
        """
        if block_name is None:
            block_name = self.environ.get("HTTP_X_BLOCK")
        if not block_name:
            raise ValueError(
                "block_name is required — pass it explicitly or use data-block on the form"
            )
        # Enforce strict block naming (no # prefix)
        if block_name.startswith("#"):
            raise ValueError(
                f"Invalid block name '{block_name}'. Block names should not include the '#' prefix. "
                f"Use '{block_name.lstrip('#')}' instead."
            )
        self._current_block = block_name
        if not hasattr(self, "_asok_blocks"):
            self._asok_blocks = []
        self._asok_blocks.append(block_name)
        content, tpl_root = self._resolve_template(filepath)
        return render_block_string(
            content, block_name, self._template_context(context), root_dir=tpl_root
        )

    def blocks(self: Any, filepath: str, block_names: list[str], **context: Any) -> str:
        """Render multiple blocks intelligently.

        For SPA requests (with X-Block header): Returns wrapped in <template> tags
        For normal page loads: Returns unwrapped HTML for better SEO/accessibility

        Args:
            filepath: Template file path
            block_names: List of block names to render
            **context: Template context variables

        Returns:
            String containing rendered blocks (wrapped or unwrapped based on request type)

        Example:
            # In your page handler:
            def render(request):
                return request.blocks('page.html', ['main', 'title', 'sidebar'])

            # SPA request: Returns <template data-block="main">...</template>...
            # Normal request: Returns just the HTML content
        """
        is_spa = bool(self.environ.get("HTTP_X_BLOCK"))
        content, tpl_root = self._resolve_template(filepath)

        parts = []
        for name in block_names:
            self._current_block = name
            tpl_ctx = self._template_context(context)
            block_html = render_block_string(content, name, tpl_ctx, root_dir=tpl_root)

            if is_spa:
                parts.append(f'<template data-block="{name}">{block_html}</template>')
            else:
                parts.append(block_html)

        return "\n".join(parts)

    def static(self: Any, filepath: str) -> str:
        """Return the public URL for a static asset, with versioning/optimization."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        root = self.environ.get("asok.root", os.getcwd())

        target_path = filepath
        # Smart WebP Swap
        if is_image(filepath) and not filepath.endswith(".webp"):
            # Try both: image.webp and image.jpg.webp
            webp_candidates = [
                filepath.rsplit(".", 1)[0] + ".webp",
                filepath + ".webp",
            ]
            for webp_path in webp_candidates:
                full_parts = os.path.join(root, "src/partials", webp_path.lstrip("/"))
                full_uploads = os.path.join(
                    root, "src/partials/uploads", webp_path.lstrip("/")
                )
                if os.path.isfile(full_parts) or os.path.isfile(full_uploads):
                    target_path = webp_path
                    break

        # Smart Min Swap (JS/CSS)
        elif app_ref and not app_ref.config.get("DEBUG"):
            if filepath.endswith(".js") and not filepath.endswith(".min.js"):
                min_path = filepath.rsplit(".", 1)[0] + ".min.js"
                # Check if min version exists in parts
                full_min = os.path.join(root, "src/partials", min_path.lstrip("/"))
                if os.path.isfile(full_min):
                    target_path = min_path
            elif (
                filepath.endswith(".css")
                and not filepath.endswith(".min.css")
                and not filepath.endswith(".build.css")
            ):
                min_path = filepath.rsplit(".", 1)[0] + ".min.css"
                full_min = os.path.join(root, "src/partials", min_path.lstrip("/"))
                if os.path.isfile(full_min):
                    target_path = min_path

        url = "/" + target_path.lstrip("/")
        if app_ref and not app_ref.config.get("DEBUG"):
            h = app_ref._static_hash(target_path)
            if h:
                url += f"?v={h}"
        elif app_ref and app_ref.config.get("DEBUG"):
            url += f"?v={int(time.time())}"
        return url
