from __future__ import annotations

import html
import json
from typing import Any, Callable

from asok.templates import SafeString


def html_safe_json(data: Any) -> str:
    """Encode data as JSON and escape it for safe use in HTML attributes.

    SECURITY: This function properly escapes JSON for use in HTML attributes,
    preventing XSS attacks through asok-state and other JSON-in-HTML contexts.

    Unlike json.dumps().replace('"', '&quot;'), this handles:
    - HTML special characters (<, >, &, ", ')
    - Newlines and control characters that could break attributes
    - Already-escaped content (via double encoding prevention)
    """
    # Serialize to JSON
    json_str = json.dumps(data, ensure_ascii=True)

    # Escape for HTML attributes (handles <, >, &, ", ')
    # ensure_ascii=True already converts non-ASCII to \uXXXX, preventing encoding issues
    escaped = html.escape(json_str, quote=True)

    return escaped


def _merge_attrs(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge base attributes with overrides, stripping trailing underscores from keys."""
    merged = dict(base)
    for k, v in overrides.items():
        key = k.rstrip("_") if k.endswith("_") and k != "_" else k
        merged[key] = v
    return merged


def _filter_nested_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Filter out attributes meant for nested elements (containing '__')."""
    return {k: v for k, v in attrs.items() if "__" not in k}


class Renderable:
    """A lazy-rendering wrapper for HTML elements.

    Supports both direct string conversion: `{{ field.label }}`
    and parameter-based invocation: `{{ field.label(class='btn') }}`.
    """

    def __init__(self, render_fn: Callable[..., str]):
        self._render_fn = render_fn

    def __str__(self) -> str:
        return SafeString(self._render_fn())

    def __call__(self, **attrs: Any) -> SafeString:
        return SafeString(self._render_fn(**attrs))
