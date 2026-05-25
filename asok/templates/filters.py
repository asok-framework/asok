from __future__ import annotations

import datetime
import html as _html
import re
from typing import Any

from asok.utils import humanize

from .safestring import SafeString, html_safe_json

_RE_STRIPTAGS = re.compile(r"<[^>]+>")


def _decode_base64_filter(value: Any, **attrs: Any) -> SafeString:
    """Décode une chaîne base64 (signature, image) en balise <img>.

    Filtre de template pour afficher des images base64.

    Usage:
        {{ user.signature | decode_base64 }}
        {{ user.signature | decode_base64(class_="w-64 border border-gray-300") }}
    """
    if not value:
        return SafeString("")

    # Construire les attributs HTML
    attr_parts = []
    for k, v in attrs.items():
        # Retirer le underscore final (class_ -> class)
        key = k.rstrip("_") if k.endswith("_") and k != "_" else k
        if v is True:
            attr_parts.append(_html.escape(key))
        elif v is not False and v is not None:
            attr_parts.append(f'{_html.escape(key)}="{_html.escape(str(v))}"')

    attr_str = " ".join(attr_parts)
    if attr_str:
        attr_str = " " + attr_str

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


TEMPLATE_FILTERS = {
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "capitalize": lambda v: str(v).capitalize(),
    "title": lambda v: str(v).title(),
    "truncate": _truncate_filter,
    "replace": _replace_filter,
    "join": lambda v, sep=", ": sep.join(str(i) for i in v),
    "default": lambda v, d="": d if v is None or v == "" else v,
    "striptags": lambda v: _RE_STRIPTAGS.sub("", str(v)),
    "length": lambda v: len(v),
    "date": lambda v, f="%d/%m/%Y": _date_filter(v, f),
    "pluralize": lambda v, s, p: s if int(v) <= 1 else p,
    "safe": lambda v: SafeString(str(v)) if v is not None else SafeString(""),
    "escape": lambda v: _html.escape(str(v)) if v is not None else "",
    "e": lambda v: _html.escape(str(v)) if v is not None else "",
    "first": lambda v: v[0] if v and len(v) > 0 else None,
    "last": lambda v: v[-1] if v and len(v) > 0 else None,
    "selectattr": lambda v, attr, val=True: [
        i
        for i in v
        if (
            getattr(i, attr)
            if hasattr(i, attr)
            else (i.get(attr) if isinstance(i, dict) else None)
        )
        == val
    ],
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
}
