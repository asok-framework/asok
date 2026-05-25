from __future__ import annotations

import html as _html
import json
from typing import Any, Union


class SafeString(str):
    """Marks a string as safe HTML to prevent automatic escaping during rendering."""

    pass


def html_safe_json(v: Any, **kwargs: Any) -> SafeString:
    """Serialize object to JSON and escape <, >, & for safe inclusion in <script> tags."""
    json_str = json.dumps(v, **kwargs)
    return SafeString(
        json_str.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _extract_nested_attrs(attrs: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Helper to extract attributes starting with a prefix (e.g., 'option__class' -> 'class')."""
    res = {}
    prefix_sep = f"{prefix}__"
    for k, v in list(attrs.items()):
        if k.startswith(prefix_sep):
            key = k[len(prefix_sep) :]
            # Handle class_ -> class
            key = key.rstrip("_") if key.endswith("_") and key != "_" else key
            res[key] = v
    return res


def _render_attrs(attrs: dict[str, Any]) -> str:
    """Render a dictionary of attributes into a space-separated HTML string."""
    parts = ""
    for k, v in attrs.items():
        if v is True:
            parts += f" {_html.escape(k)}"
        elif v is not False and v is not None:
            parts += f' {_html.escape(k)}="{_html.escape(str(v))}"'
    return parts


def _escape(value: Any) -> Union[str, SafeString]:
    """Escape a value for safe HTML output. SafeString instances are returned unchanged."""
    if value is None:
        return ""
    if isinstance(value, SafeString):
        return value
    s = str(value)
    if isinstance(s, SafeString):
        return s
    return _html.escape(s, quote=True)
