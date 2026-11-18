from __future__ import annotations

import datetime
import html as _html
import re
from typing import Any, Optional

from asok.utils import humanize

from .safestring import SafeString, html_safe_json
from .sandbox import _get
from .undefined import Undefined

_RE_STRIPTAGS = re.compile(r"<[^>]+>")


def _clean_attr_key(k: str) -> str:
    if k.endswith("_") and k != "_":
        return k.rstrip("_")
    return k


def _format_attr_part(key: str, v: Any) -> Optional[str]:
    if v is True:
        return _html.escape(key)
    if v is not False and v is not None:
        return f'{_html.escape(key)}="{_html.escape(str(v))}"'
    return None


def _build_html_attrs(attrs: dict[str, Any]) -> str:
    attr_parts = []
    for k, v in attrs.items():
        key = _clean_attr_key(k)
        part = _format_attr_part(key, v)
        if part:
            attr_parts.append(part)

    attr_str = " ".join(attr_parts)
    return " " + attr_str if attr_str else ""


def _decode_base64_filter(value: Any, **attrs: Any) -> SafeString:
    """Decode a base64 string (signature, image) into an <img> tag.

    Template filter to display base64 images.

    Usage:
        {{ user.signature | decode_base64 }}
        {{ user.signature | decode_base64(class_="w-64 border border-gray-300") }}
    """
    if not value:
        return SafeString("")
    attr_str = _build_html_attrs(attrs)
    return SafeString(f'<img src="{_html.escape(value)}"{attr_str} alt="Image">')


def _date_filter(v: Any, f: str = "%d/%m/%Y") -> Any:
    """Format a date/datetime or ISO string."""
    if hasattr(v, "strftime"):
        return v.strftime(f)
    if isinstance(v, str) and len(v) >= 10:
        try:
            return datetime.datetime.fromisoformat(v).strftime(f)
        except (ValueError, TypeError):
            return v
    return v


def _as_epoch_seconds(v: Any) -> Optional[float]:
    """Return v as epoch seconds if it is a plausible timestamp (~2001-2286)."""
    try:
        ts = float(v)
    except (ValueError, TypeError):
        return None
    return ts if 1_000_000_000 <= ts <= 9_999_999_999 else None


def _format_epoch(ts: float, f: str, fallback: Any) -> Any:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime(f)
    except (ValueError, OSError, OverflowError):
        return fallback


def _epoch_filter(v: Any, f: str = "%d/%m/%Y %H:%M") -> Any:
    """Format a value that may be a Unix epoch (int/float/numeric str) as a date.

    Datetime objects and ISO strings are formatted too. Anything that is not a
    plausible epoch (roughly 2001-2286) is returned unchanged, so small integers
    like ids are never mistaken for timestamps.
    """
    if hasattr(v, "strftime"):
        return v.strftime(f)
    ts = _as_epoch_seconds(v)
    if ts is not None:
        return _format_epoch(ts, f, v)
    if isinstance(v, str):
        return _date_filter(v, f)
    return v


def _truncate_filter(v: Any, length: int = 100) -> str:
    """Truncate a string to a maximum length.

    SECURITY: Validates length parameter to prevent negative or excessive values.
    """
    # SECURITY: Validate length parameter
    if not isinstance(length, int) or length < 0:
        length = 100
    if length > 1_000_000:  # Max 1MB
        length = 1_000_000

    s = str(v)
    return s[:length] + "..." if len(s) > length else s


def _replace_filter(v: Any, old: str, new: str, count: int = -1) -> str:
    """Replace occurrences of a substring.

    SECURITY: Limits replacement count to prevent DoS with excessive replacements.
    """
    # SECURITY: Limit replacement count to prevent DoS (max 10,000 replacements)
    if count < 0 or count > 10_000:
        count = 10_000

    return str(v).replace(str(old), str(new), count)


def _fallback_display_attribute(value: Any) -> Optional[str]:
    for attr in ("name", "title", "label", "email", "username", "slug"):
        v = getattr(value, attr, None)
        if v is not None:
            return str(v)
    return None


def _fallback_display(value: Any) -> str:
    s = str(value)
    if not s.startswith("<") or "id=" not in s:
        return s
    attr_val = _fallback_display_attribute(value)
    if attr_val is not None:
        return attr_val
    return f"#{getattr(value, 'id', '?')}"


_cached_display = None
_display_lookup_done = False


def _display_filter(value: Any) -> str:
    """Safely format database model instances or other values using _display helper."""
    global _cached_display, _display_lookup_done
    if value is None:
        return ""
    if not _display_lookup_done:
        try:
            from asok.admin.utils import _display

            _cached_display = _display
        except ImportError:
            _cached_display = None
        _display_lookup_done = True

    if _cached_display is not None:
        return _cached_display(value)
    return _fallback_display(value)


def _pluralize_filter(
    v: Any, singular_or_plural: str = "s", plural: Optional[str] = None
) -> str:
    """Format pluralization based on value or collection length.

    Supports:
        - {{ 1 | pluralize("s") }} -> ""
        - {{ 2 | pluralize("s") }} -> "s"
        - {{ 1 | pluralize("y", "ies") }} -> "y"
        - {{ 2 | pluralize("y", "ies") }} -> "ies"
        - {{ 1 | pluralize("item", "items") }} -> "item"
        - {{ 2 | pluralize("item", "items") }} -> "items"
        - Works with collections e.g. {{ items | pluralize("item", "items") }}
    """
    if plural is None:
        singular = ""
        plural_str = singular_or_plural
    else:
        singular = singular_or_plural
        plural_str = plural

    try:
        if isinstance(v, (list, tuple, dict, set)):
            count = len(v)
        else:
            count = float(v)
    except (ValueError, TypeError):
        count = 1

    if count == 1:
        return singular
    return plural_str


def _fallback_markdown_links(s: str) -> SafeString:
    from asok.utils.html_sanitizer import _is_safe_url

    parts: list[str] = []
    last_idx = 0
    for m in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", s):
        parts.append(_html.escape(s[last_idx : m.start()]))
        label, url = m.group(1), m.group(2).strip()
        if _is_safe_url(url):
            parts.append(
                f'<a href="{_html.escape(url)}" target="_blank" rel="noopener"'
                f' class="underline hover:opacity-80">{_html.escape(label)}</a>'
            )
        else:
            parts.append(_html.escape(label))
        last_idx = m.end()
    parts.append(_html.escape(s[last_idx:]))
    return SafeString("".join(parts))


def _strip_single_paragraph(html: str) -> str:
    if html.startswith("<p>") and html.endswith("</p>") and html.count("<p>") == 1:
        return html[3:-4]
    return html


def _markdown_to_html_str(s: str) -> str:
    try:
        import markdown

        from asok.utils.html_sanitizer import sanitize_html

        raw_html = markdown.markdown(s)
        return _strip_single_paragraph(sanitize_html(raw_html))
    except ImportError:
        return str(_fallback_markdown_links(s))


def _markdown_filter(v: Any) -> SafeString:
    """Convert Markdown text into safe HTML string.

    Supports Python 'markdown' package if installed, or falls back to
    regex link conversion [label](url) -> <a href="url">label</a>.
    """
    if not v:
        return SafeString("")
    return SafeString(_markdown_to_html_str(str(v)))


class _FiltersDict(dict):
    def __missing__(self, key: str):
        from asok.exceptions import TemplateError

        available = ", ".join(sorted(self.keys()))
        raise TemplateError(
            f"Unknown template filter '{key}'. Available filters: {available}"
        )


TEMPLATE_FILTERS = _FiltersDict(
    {
        "string": lambda v: str(v) if v is not None else "",
        "upper": lambda v: str(v).upper(),
        "lower": lambda v: str(v).lower(),
        "capitalize": lambda v: str(v).capitalize(),
        "title": lambda v: str(v).title(),
        "truncate": _truncate_filter,
        "replace": _replace_filter,
        "join": lambda v, sep=", ": sep.join(str(i) for i in v),
        "default": lambda v, d="": (
            d if isinstance(v, Undefined) or v is None or v == "" else v
        ),
        "striptags": lambda v: _RE_STRIPTAGS.sub("", str(v)),
        "length": lambda v: len(v),
        "date": lambda v, f="%d/%m/%Y": _date_filter(v, f),
        "epoch": lambda v, f="%d/%m/%Y %H:%M": _epoch_filter(v, f),
        "pluralize": _pluralize_filter,
        "safe": lambda v: SafeString(str(v)) if v is not None else SafeString(""),
        "escape": lambda v: _html.escape(str(v)) if v is not None else "",
        "e": lambda v: _html.escape(str(v)) if v is not None else "",
        "first": lambda v: v[0] if v and len(v) > 0 else None,
        "last": lambda v: v[-1] if v and len(v) > 0 else None,
        "selectattr": lambda v, attr, val=True: [i for i in v if _get(i, attr) == val],
        "abs": lambda v: abs(v),
        "tojson": lambda v, **kwargs: html_safe_json(v, **kwargs),
        "dump": lambda v, **kwargs: html_safe_json(v, **kwargs),
        "dictsort": lambda v: sorted(v.items()) if isinstance(v, dict) else v,
        # Humanize filters
        "time_ago": humanize.time_ago,
        "filesize": humanize.file_size,
        "intcomma": humanize.intcomma,
        "duration": humanize.duration,
        # Base64 decoding filter for signatures and images
        "decode_base64": _decode_base64_filter,
        "display": _display_filter,
        "markdown": _markdown_filter,
    }
)
