from __future__ import annotations

import logging
import os
from typing import Any

from ..utils.minify import minify_css, minify_js
from ._asset_injector import AssetInjector, precompile_directives
from ._expr_validator import (
    expression_has_await as _expression_has_await,
)
from ._expr_validator import (
    is_safe_expression as _is_safe_expression,
)

logger = logging.getLogger("asok.assets")


class AssetMixin:
    def get_asset(self, filename: str) -> str:
        """Retrieve an asset file's contents, caching in production."""
        if not hasattr(self, "_asset_cache"):
            self._asset_cache = {}
        debug = self.config.get("DEBUG", False)
        filename, is_pre_minified = self._maybe_swap_for_min(filename, debug)
        cached = self._asset_cache.get(filename) if not debug else None
        if cached is not None:
            return cached
        return self._load_asset(filename, debug, is_pre_minified)

    def _load_asset(self, filename: str, debug: bool, is_pre_minified: bool) -> str:
        content = self._read_asset_file(filename)
        if debug:
            return content
        if not is_pre_minified:
            content = self._minify_asset(filename, content)
        self._asset_cache[filename] = content
        return content

    @staticmethod
    def _maybe_swap_for_min(filename: str, debug: bool) -> tuple[str, bool]:
        if debug:
            return filename, False
        base, ext = os.path.splitext(filename)
        if base.endswith(".min") or ext not in (".js", ".css"):
            return filename, False
        min_filename = f"{base}.min{ext}"
        min_path = os.path.join(os.path.dirname(__file__), "assets", min_filename)
        if os.path.exists(min_path):
            return min_filename, True
        return filename, False

    @staticmethod
    def _read_asset_file(filename: str) -> str:
        path = os.path.join(os.path.dirname(__file__), "assets", filename)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _minify_asset(filename: str, content: str) -> str:
        if filename.endswith(".js"):
            return minify_js(content)
        if filename.endswith(".css"):
            return minify_css(content)
        return content

    # SECURITY: validators are exposed as bound lru_cache callables so callers
    # (and the test suite) can clear/inspect the cache.
    _validate_expression_cached = staticmethod(_is_safe_expression)
    _is_async_expression_cached = staticmethod(_expression_has_await)

    def _validate_directive_expression(self, expr: str) -> bool:
        return self._validate_expression_cached(expr)

    def _precompile_directives(self, html: str) -> tuple[str, dict[str, str]]:
        """Pre-compile Asok directives into a hash-based registry for CSP Zero-Eval."""
        return precompile_directives(self, html)

    def _inject_assets(
        self,
        content: str,
        request: Any,
        nonce: str,
        stream: bool = False,
        include_scripts: bool = True,
        only_scripts: bool = False,
    ) -> str:
        """Inject required CSRF tags, metadata, and scripts into the HTML response."""
        if not isinstance(content, str):
            return content
        injector = AssetInjector(
            self, request, content, nonce, stream, include_scripts, only_scripts
        )
        return injector.inject()
