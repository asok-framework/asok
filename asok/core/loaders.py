from __future__ import annotations

import importlib.util
from typing import Any


class LoaderMixin:
    """Mixin class for Asok that handles dynamic module loading and template reading."""

    def _load_module(self, page_file: str) -> Any:
        """Dynamically load a Python module from a page file."""
        debug = self.config.get("DEBUG")

        if not debug and page_file in self._module_cache:
            return self._module_cache[page_file]

        spec = importlib.util.spec_from_file_location(
            f"page_{id(page_file)}", page_file
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not debug:
            self._module_cache[page_file] = module

        return module

    def _read_template(self, path: str) -> str:
        """Read a template file from disk, using a cache in production."""
        debug = self.config.get("DEBUG")

        if not debug and path in self._template_cache:
            return self._template_cache[path]

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if not debug:
            self._template_cache[path] = content

        return content
