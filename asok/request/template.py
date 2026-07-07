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

    _ASOK_DIRECTIVES = [
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

    def _initial_template_path(self: Any, filepath: str, root: str) -> str:
        """Build the initial absolute path for a template file."""
        if (
            not os.path.isabs(filepath)
            and not filepath.startswith("src/")
            and hasattr(self, "_current_page_file")
            and self._current_page_file
        ):
            return os.path.join(os.path.dirname(self._current_page_file), filepath)
        return os.path.join(root, filepath)

    def _swap_extension(path: str) -> str:
        base_path, current_ext = os.path.splitext(path)
        target = (
            ".asok"
            if current_ext == ".html"
            else (".html" if current_ext == ".asok" else None)
        )
        if target and os.path.isfile(base_path + target):
            return base_path + target
        return path

    def _try_resolve_extensions(path: str) -> str:  # noqa: N805
        """Attempt to resolve a template path by appending or swapping extensions."""
        for ext in (".html", ".asok"):
            if os.path.isfile(path + ext):
                return path + ext
        return TemplateMixin._swap_extension(path)

    def _is_partial_template(normalized: str) -> bool:
        return any(
            d in normalized
            for d in ("/partials/", "/html/", "/components/", "/templates/")
        )

    def _validate_template_name(path: str) -> None:  # noqa: N805
        """Raise ValueError if a page template has an invalid filename."""
        if TemplateMixin._is_partial_template(path.replace("\\", "/")):
            return
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

    def _search_template_roots(self: Any, root: str, filepath: str) -> str | None:
        for r in self._template_search_roots(root):
            cand = os.path.join(r, filepath)
            if not os.path.isfile(cand):
                cand = TemplateMixin._try_resolve_extensions(cand)
            if os.path.isfile(cand):
                return cand
        return None

    def _read_resolved_template(self: Any, path: str) -> str:
        app = self.environ.get("asok.app")
        if app:
            return app._read_template(path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def _resolve_template_file_path(self: Any, filepath: str, root: str) -> str:
        path = self._initial_template_path(filepath, root)
        if not os.path.isfile(path):
            path = TemplateMixin._try_resolve_extensions(path)
        if not os.path.isfile(path):
            path = self._search_template_roots(root, filepath) or path
        return path

    def _check_asok_directives(self: Any, content: str) -> None:
        if any(attr in content for attr in TemplateMixin._ASOK_DIRECTIVES):
            self._asok_needs_directives = True

    def _resolve_template(self: Any, filepath: str) -> tuple[str, Any]:
        """Convert a template path to its content and identify the partials roots."""
        root = self.environ.get("asok.root", os.getcwd())
        path = self._resolve_template_file_path(filepath, root)
        TemplateMixin._validate_template_name(path)
        content = self._read_resolved_template(path)
        self._check_asok_directives(content)
        return content, self._template_search_roots(root)

    def _template_search_roots(self: Any, root: str) -> list[str]:
        """Project's partials dir first, then any extension templates dir."""
        roots = [os.path.join(root, "src/partials")]
        app = self.environ.get("asok.app")
        ext_paths = getattr(app, "_extension_template_paths", None) or []
        for p in ext_paths:
            if p not in roots:
                roots.append(p)
        return roots

    def share(self: Any, name: str, value: Any = None) -> Union[str, Any]:
        """Set or get a shared variable bound to this request.

        If a value is provided, it is stored in the per-request cache.
        If no value is provided, it returns the shared value.
        """
        if value is not None:
            self.__dict__.setdefault("_shared_cache", {})[name] = value
            return ""
        return self.shared(name)

    def _get_shared_raw_value(self, name: str, cache: dict) -> tuple[Any, bool]:
        val = cache.get(name)
        was_in_cache = name in cache
        if val is not None or was_in_cache:
            return val, was_in_cache

        app_ref = self.environ.get("asok.app")
        if not app_ref or name not in getattr(app_ref, "_shared", {}):
            return None, False

        return app_ref._shared[name], False

    def _is_request_factory(self, val: Any) -> bool:
        try:
            import inspect

            sig = inspect.signature(val)
            params = list(sig.parameters.keys())
            return bool(params and params[0] in ("request", "req"))
        except Exception:
            return False

    def _is_form_template(self, val: Any) -> bool:
        from asok.forms import Form

        return isinstance(val, Form) and getattr(val, "_is_template", False)

    def _resolve_shared_value(self, val: Any) -> Any:
        if self._is_form_template(val):
            return val._bind(self)
        if callable(val) and self._is_request_factory(val):
            return val(self)
        return val

    def shared(
        self: Any, name: str, expected_type: Optional[Type[T]] = None
    ) -> Union[T, Any]:
        """Get a shared variable bound to this request (cached per request).

        Variables registered via app.share() or request.share() are auto-resolved.
        Callables are invoked with the current request, and Form templates are bound.
        Providing an 'expected_type' enables IDE autocompletion for the returned object.
        """
        cache = self.__dict__.setdefault("_shared_cache", {})
        val, was_in_cache = self._get_shared_raw_value(name, cache)

        if was_in_cache:
            return val

        resolved = self._resolve_shared_value(val)
        cache[name] = resolved
        return resolved

    def shared_form(self: Any, name: str) -> Any:
        """Typed internal helper to get a shared form with full IDE autocompletion.

        Returns an instance of Form automatically bound to this request.
        """
        from asok.forms import Form

        return self.shared(name, Form)

    def _inject_shared_vars(self: Any, ctx: dict[str, Any]) -> None:
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

    def _template_context(self: Any, context: dict[str, Any]) -> dict[str, Any]:
        """Assemble the standard context variables for template rendering."""
        ctx = {
            "request": self,
            "nonce": getattr(self, "nonce", ""),
            "__": self.__,
            "static": self.static,
            "get_flashed_messages": self.get_flashed_messages,
            "meta": self.meta,
            "csrf_input": getattr(self, "csrf_input", None),
            "csrf_token": getattr(self, "csrf_token_value", ""),
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

            client = kwargs.pop("client", None)
            slot = kwargs.pop("slot", None)
            if slot is not None:
                slot = SafeString(slot)

            if saved_signed:
                try:
                    instance = cls._from_signed_state(saved_signed, secret, cid=cid)
                    if instance is not None:
                        instance._slot = slot
                        instance._client = client
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

            instance = cls(_cid=cid, _client=client, **kwargs)
            instance._slot = slot
            # Save initial state so it survives a refresh
            signed_state = instance._sign_state(secret)
            sess[f"_comp_{cid}"] = signed_state
            return SafeString(str(instance))

        ctx["component"] = component
        self._inject_shared_vars(ctx)
        ctx.update(context)
        return ctx

    def _render_multiple_blocks(
        self: Any, filepath: str, names: list[str], **context: Any
    ) -> str:
        parts = []
        for name in names:
            content = self.block(filepath, name, **context)
            # SECURITY: escape block name to prevent XSS via crafted X-Block header.
            parts.append(
                f'<template data-block="{html.escape(name, quote=True)}">{content}</template>'
            )
        return "".join(parts)

    def _render_block_header(
        self: Any, filepath: str, block_header: str, **context: Any
    ) -> str:
        names = [b.strip().lstrip("#") for b in block_header.split(",") if b.strip()]
        try:
            self._validate_block_names(names)
            if len(names) == 1:
                return self.block(filepath, names[0], **context)
            return self._render_multiple_blocks(filepath, names, **context)
        except ValueError:
            content, tpl_root = self._resolve_template(filepath)
            return render_template_string(
                content,
                self._template_context(context),
                root_dir=tpl_root,
                inject_block_markers=True,
            )

    def html(self: Any, filepath: str, **context: Any) -> str:
        """Render an HTML template and return the result as a string."""
        # Auto-detect block request (from data-block JS swap)
        block_header = self.environ.get("HTTP_X_BLOCK")
        if block_header:
            return self._render_block_header(filepath, block_header, **context)

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

    def _validate_block_names(self: Any, names: list[str]) -> None:
        """Raise ValueError if any block name starts with '#'."""
        for name in names:
            if name and name.startswith("#"):
                raise ValueError(
                    f"Invalid block name '{name}'. Use the block name directly without the '#' prefix."
                )

    def _yield_scoped_css(self: Any, page_id: str) -> Iterator[str]:
        """Yield a <style> tag with scoped and possibly minified CSS."""
        if not (self.scoped_assets.get("css") and page_id):
            return
        try:
            from asok.utils.css import scope_css
            from asok.utils.minify import minify_css

            with open(self.scoped_assets["css"], "r", encoding="utf-8") as f:
                raw_css = f.read()
            scoped_css = scope_css(raw_css, page_id)
            if not self.environ.get("DEBUG"):
                scoped_css = minify_css(scoped_css)
            safe_page_id = html.escape(page_id, quote=True)
            safe_css = scoped_css.replace("</style>", r"<\/style>")
            yield f'<style id="asok-scoped-css" data-page-id="{safe_page_id}">{safe_css}</style>'
        except Exception:
            pass

    def _yield_scoped_js(self: Any, page_id: Optional[str]) -> Iterator[str]:
        """Yield a <script> tag with scoped and possibly minified JS."""
        if not self.scoped_assets.get("js"):
            return
        try:
            from asok.utils.js import scope_js
            from asok.utils.minify import minify_js

            with open(self.scoped_assets["js"], "r", encoding="utf-8") as f:
                raw_js = f.read()
            scoped_js = scope_js(raw_js, page_id) if page_id else raw_js
            if not self.environ.get("DEBUG"):
                scoped_js = minify_js(scoped_js)
            nonce = getattr(self, "nonce", "")
            safe_js = scoped_js.replace("</script>", r"<\/script>")
            yield f"<script id=\"asok-scoped-js\" nonce=\"{nonce}\">(function(){{const init=function(){{{safe_js}}};if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();}})();</script>"
        except Exception:
            pass

    def _yield_block_item(
        self: Any,
        name: str,
        tpl_source: str,
        tpl_root: str,
        is_spa_request: bool,
        **context: Any,
    ) -> Iterator[str]:
        """Yield a single rendered block. Uses 'tpl_source' (not 'content') so that
        a view calling stream(..., content=html) can pass 'content' through **context
        as a Jinja2 variable without causing a duplicate-argument TypeError."""
        if not name:
            return
        self._current_block = name
        tpl_ctx = self._template_context(context)
        block_html = render_block_string(tpl_source, name, tpl_ctx, root_dir=tpl_root)
        if is_spa_request:
            # SECURITY: escape block name to prevent XSS via crafted X-Block header.
            yield f'<template data-block="{html.escape(name, quote=True)}">{block_html}</template>'
        else:
            yield block_html

    def _yield_scoped_assets(self: Any, is_spa_request: bool) -> Iterator[str]:
        """Yield scoped CSS/JS chunks for SPA block responses."""
        if is_spa_request and hasattr(self, "scoped_assets"):
            page_id = getattr(self, "page_id", None)
            yield from self._yield_scoped_css(page_id)
            yield from self._yield_scoped_js(page_id)

    def _stream_blocks(
        self: Any, filepath: str, block_header: str, **context: Any
    ) -> Iterator[str]:
        """Internal helper to stream multiple template blocks.

        Wraps content in <template> tags ONLY for SPA block requests.
        For normal page loads, returns unwrapped content for better SEO and first paint.
        """
        names = [b.strip() for b in block_header.split(",")]
        self._validate_block_names(names)
        tpl_content, tpl_root = self._resolve_template(filepath)
        is_spa_request = bool(self.environ.get("HTTP_X_BLOCK"))

        for name in names:
            yield from self._yield_block_item(
                name, tpl_content, tpl_root, is_spa_request, **context
            )

        # CRITICAL: Include scoped CSS/JS in SPA block responses
        yield from self._yield_scoped_assets(is_spa_request)

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
                parts.append(
                    f'<template data-block="{html.escape(name, quote=True)}">{block_html}</template>'
                )
            else:
                parts.append(block_html)

        return "\n".join(parts)

    def _resolve_webp_path(self: Any, filepath: str, root: str) -> str:
        """Return a WebP variant of the path if one exists on disk."""
        candidates = [filepath.rsplit(".", 1)[0] + ".webp", filepath + ".webp"]
        for webp_path in candidates:
            full_parts = os.path.join(root, "src/partials", webp_path.lstrip("/"))
            full_uploads = os.path.join(
                root, "src/partials/uploads", webp_path.lstrip("/")
            )
            if os.path.isfile(full_parts) or os.path.isfile(full_uploads):
                return webp_path
        return filepath

    def _resolve_min_js(filepath: str, root: str) -> str:  # noqa: N805
        """Return the minified JS path if a .min.js file exists."""
        if not filepath.endswith(".js") or filepath.endswith(".min.js"):
            return filepath
        min_path = filepath.rsplit(".", 1)[0] + ".min.js"
        if os.path.isfile(os.path.join(root, "src/partials", min_path.lstrip("/"))):
            return min_path
        return filepath

    def _resolve_min_css(filepath: str, root: str) -> str:  # noqa: N805
        """Return the minified CSS path if a .min.css file exists."""
        if not filepath.endswith(".css") or filepath.endswith(
            (".min.css", ".build.css")
        ):
            return filepath
        min_path = filepath.rsplit(".", 1)[0] + ".min.css"
        if os.path.isfile(os.path.join(root, "src/partials", min_path.lstrip("/"))):
            return min_path
        return filepath

    def _resolve_minified_path(
        self: Any, filepath: str, app_ref: Any, root: str
    ) -> str:
        if not app_ref or app_ref.config.get("DEBUG"):
            return filepath
        new_path = TemplateMixin._resolve_min_js(filepath, root)
        if new_path != filepath:
            return new_path
        return TemplateMixin._resolve_min_css(filepath, root)

    def _resolve_static_target(
        self: Any, filepath: str, app_ref: Any, root: str
    ) -> str:
        """Resolve the best available static asset path (WebP swap or minified)."""
        if is_image(filepath) and not filepath.endswith(".webp"):
            return self._resolve_webp_path(filepath, root)
        return self._resolve_minified_path(filepath, app_ref, root)

    def _build_static_url(target_path: str) -> str:  # noqa: N805
        """Build the public URL for a static asset, serving from S3 if configured."""
        if os.environ.get("ASOK_SERVE_STATIC_FROM_S3", "false").lower() != "true":
            return "/" + target_path.lstrip("/")
        try:
            from asok.core.storage import S3Storage, get_storage

            storage = get_storage()
            if isinstance(storage, S3Storage):
                return storage.url(target_path.lstrip("/"))
        except Exception:
            pass
        return "/" + target_path.lstrip("/")

    def _append_static_version(url: str, target_path: str, app_ref: Any) -> str:  # noqa: N805
        """Append a cache-busting version query param to the static URL."""
        if not app_ref:
            return url
        if not app_ref.config.get("DEBUG"):
            h = app_ref._static_hash(target_path)
            if h:
                return url + f"?v={h}"
        else:
            return url + f"?v={int(time.time())}"
        return url

    def static(self: Any, filepath: str) -> str:
        """Return the public URL for a static asset, with versioning/optimization."""
        app_ref: Optional[Any] = self.environ.get("asok.app")
        root = self.environ.get("asok.root", os.getcwd())
        target_path = self._resolve_static_target(filepath, app_ref, root)
        url = TemplateMixin._build_static_url(target_path)
        return TemplateMixin._append_static_version(url, target_path, app_ref)
