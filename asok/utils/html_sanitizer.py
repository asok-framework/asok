"""HTML sanitizer for WYSIWYG content.

SECURITY: Prevents Stored XSS attacks by sanitizing user-generated HTML.
"""

import re
from html import escape, unescape

# Whitelist of safe HTML tags and attributes
ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "em",
    "u",
    "s",
    "del",
    "ins",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "div",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target", "rel"},
    "img": {"src", "alt", "title", "width", "height"},
    "p": {"style", "class"},
    "span": {"style", "class"},
    "div": {"style", "class"},
    "li": {"class"},
    "ol": {"class"},
    "ul": {"class"},
    "pre": {"class"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Whitelisted WYSIWYG (Quill) formatting classes preserved so the read-only view
# renders like the editor — code blocks, text alignment and indentation. A class
# token cannot execute script; anything not in this set is dropped.
_ALLOWED_CLASS_TOKENS = frozenset(
    {
        "ql-code-block-container",
        "ql-code-block",
        "ql-syntax",
        "ql-align-left",
        "ql-align-center",
        "ql-align-right",
        "ql-align-justify",
    }
    | {f"ql-indent-{i}" for i in range(1, 9)}
)

# Safe CSS properties for style attribute
ALLOWED_STYLES = {
    "color",
    "background-color",
    "font-size",
    "font-weight",
    "font-style",
    "text-align",
    "text-decoration",
    "padding",
    "margin",
}


def _check_html_limits(html: str) -> str | None:
    if not html:
        return ""
    if len(html) > 1_000_000:
        return escape(html[:1000]) + "... [content too large]"
    return None


def _clean_dangerous_html_patterns(html: str) -> str:
    # Remove comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # Remove dangerous tags entirely
    dangerous_tags = r"<\s*(script|style|iframe|object|embed|applet|link|meta|base|form|input|textarea|button)[^>]*?>.*?</\1>|<\s*(script|style|iframe|object|embed|applet|link|meta|base|form|input|textarea|button)[^>]*?/?>"
    html = re.sub(dangerous_tags, "", html, flags=re.IGNORECASE | re.DOTALL)

    # Remove event handlers (onclick, onerror, onload, etc.)
    html = re.sub(r'\s*on\w+\s*=\s*["\'][^"\']*["\']', "", html, flags=re.IGNORECASE)
    html = re.sub(r"\s*on\w+\s*=\s*\w+", "", html, flags=re.IGNORECASE)

    # Remove javascript: protocol (remove entire value)
    html = re.sub(
        r'(href|src)\s*=\s*["\']javascript:[^"\']*["\']', "", html, flags=re.IGNORECASE
    )

    # Remove data: protocol except for images
    html = re.sub(
        r'(<img[^>]+src\s*=\s*["\'])data:(?!image/)', r"\1", html, flags=re.IGNORECASE
    )
    html = re.sub(r'(<a[^>]+href\s*=\s*["\'])data:', r"\1", html, flags=re.IGNORECASE)
    return html


def _process_tag_match(match: re.Match, result: list[str]) -> None:
    closing = match.group(1)
    tag = match.group(2).lower()
    attrs = match.group(3)

    if tag in ALLOWED_TAGS:
        if closing:
            result.append(f"</{tag}>")
        else:
            sanitized_attrs = _sanitize_attributes(tag, attrs)
            if sanitized_attrs:
                result.append(f"<{tag} {sanitized_attrs}>")
            else:
                result.append(f"<{tag}>")


def _emit_text(text: str) -> str:
    """Escape a text node exactly once.

    Decode existing entities first, then re-escape — so content that already
    carries entities (e.g. `Future&lt;String&gt;` inside a Quill code block) is
    NOT double-escaped into a visible ``&lt;``. This also makes sanitization
    idempotent (safe to run again, e.g. admin-write + ORM-write). Because we
    re-escape immediately, decoded ``<``/``>``/``&`` can never form a live tag in
    the output — a text ``<script>`` stays inert, escaped text.
    """
    return escape(unescape(text))


def _add_text_before_tag(
    html: str, pos: int, match_start: int, result: list[str]
) -> None:
    text_before = html[pos:match_start]
    if text_before:
        result.append(_emit_text(text_before))


def sanitize_html(html: str) -> str:
    """Sanitize HTML content to prevent XSS attacks.

    This is a lightweight, regex-based sanitizer for trusted-ish WYSIWYG editor
    content. It relies on re-serialization (unknown tags dropped, allowed tags
    re-emitted with only allow-listed, escaped attributes) rather than the
    pre-cleaning regexes, which makes the common XSS vectors hard to slip past.

    SECURITY — best-effort, NOT a hardened boundary. Regex-based sanitizers
    cannot fully model browser HTML parsing and carry an inherent residual risk
    of mutation-XSS (mXSS). For untrusted, high-assurance input (public
    user-generated content), prefer a DOM-based sanitizer such as ``nh3`` (Rust
    ammonia bindings) or ``bleach`` and treat this function as a convenience
    fallback only.

    Args:
        html: Raw HTML string from WYSIWYG editor

    Returns:
        Sanitized HTML safe for display

    Security:
        - Removes all script, style, iframe, object, embed tags
        - Removes on* event handlers (onclick, onerror, etc.)
        - Removes javascript: protocol in links
        - Removes data: protocol in images (except data:image/*)
        - Validates style attributes against whitelist
        - Escapes unknown tags
        - Adds rel="noopener noreferrer" to links that keep a target
        - Size limits prevent DoS via extremely large HTML
    """
    limit_result = _check_html_limits(html)
    if limit_result is not None:
        return limit_result

    html = _clean_dangerous_html_patterns(html)

    result = []
    pos = 0

    # Simple tag parser (not a full HTML parser, but sufficient for WYSIWYG content)
    tag_pattern = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)

    # SECURITY: Limit number of tags processed to prevent DoS
    tag_count = 0
    max_tags = 10_000

    for match in tag_pattern.finditer(html):
        tag_count += 1
        if tag_count > max_tags:
            break

        _add_text_before_tag(html, pos, match.start(), result)
        _process_tag_match(match, result)
        pos = match.end()

    # Add remaining text
    if pos < len(html):
        result.append(_emit_text(html[pos:]))

    return "".join(result)


def _sanitize_style_value(attr_value: str) -> str | None:
    val = _sanitize_style(attr_value)
    return val if val else None


def _sanitize_href_value(attr_value: str) -> str | None:
    return attr_value if _is_safe_url(attr_value) else None


def _sanitize_src_value(attr_value: str) -> str | None:
    return attr_value if _is_safe_url(attr_value, allow_data_images=True) else None


def _sanitize_class_value(attr_value: str) -> str | None:
    """Keep only whitelisted WYSIWYG formatting class tokens; drop the rest."""
    tokens = [t for t in attr_value.split() if t in _ALLOWED_CLASS_TOKENS]
    return " ".join(tokens) if tokens else None


def _sanitize_attr_value(attr_name: str, attr_value: str) -> str | None:
    """Validate and sanitize a single attribute value. Returns sanitized value or None to skip."""
    if attr_name == "style":
        return _sanitize_style_value(attr_value)
    if attr_name == "href":
        return _sanitize_href_value(attr_value)
    if attr_name == "src":
        return _sanitize_src_value(attr_value)
    if attr_name == "class":
        return _sanitize_class_value(attr_value)
    return attr_value


def _get_allowed_attributes(tag: str, attrs: str) -> set[str] | None:
    if not attrs:
        return None
    if tag not in ALLOWED_ATTRIBUTES:
        return None
    return ALLOWED_ATTRIBUTES[tag]


def _process_attr_match(
    match: re.Match, allowed: set[str], sanitized: list[str]
) -> None:
    attr_name = match.group(1).lower()
    if attr_name not in allowed:
        return

    m3 = match.group(3)
    attr_value = m3 if m3 is not None else match.group(4)
    val = _sanitize_attr_value(attr_name, attr_value)
    if val is not None:
        sanitized.append(f'{attr_name}="{escape(val, quote=True)}"')


def _sanitize_attributes(tag: str, attrs: str) -> str:
    """Sanitize HTML attributes for a given tag.

    SECURITY: Improved parser handles both quoted and unquoted attributes.
    """
    allowed = _get_allowed_attributes(tag, attrs)
    if allowed is None:
        return ""

    sanitized = []
    # SECURITY: Enhanced regex to match both quoted and unquoted attributes
    attr_pattern = re.compile(
        r'(\w+)\s*=\s*(?:(["\'])((?:(?!\2).)*)\2|([^\s>]+))', re.IGNORECASE
    )

    for match in attr_pattern.finditer(attrs):
        _process_attr_match(match, allowed, sanitized)

    if tag == "a":
        _enforce_link_rel(sanitized)

    return " ".join(sanitized)


def _enforce_link_rel(sanitized: list[str]) -> None:
    """Force a safe rel on links that open a new browsing context.

    SECURITY: target=_blank/_new/... gives the opened page access to
    window.opener (reverse tabnabbing) unless rel="noopener" is set. We drop any
    user-supplied rel and set our own — a duplicate rel would be malformed and
    the browser would honor the unsafe first occurrence.
    """
    if not any(s.startswith("target=") for s in sanitized):
        return
    sanitized[:] = [s for s in sanitized if not s.startswith("rel=")]
    sanitized.append('rel="noopener noreferrer"')


def _process_style_rule(rule: str) -> str | None:
    rule = rule.strip()
    if ":" not in rule:
        return None

    prop, value = rule.split(":", 1)
    prop = prop.strip().lower()
    value = value.strip()

    # Check if property is allowed
    if prop in ALLOWED_STYLES:
        # Remove dangerous values
        if re.search(r"expression|javascript|import|url\(", value, re.IGNORECASE):
            return None
        return f"{prop}: {value}"
    return None


def _sanitize_style(style: str) -> str:
    """Sanitize CSS style attribute."""
    if not style:
        return ""

    safe_rules = []
    for rule in style.split(";"):
        val = _process_style_rule(rule)
        if val is not None:
            safe_rules.append(val)

    return "; ".join(safe_rules) if safe_rules else ""


_DANGEROUS_PROTOCOLS = (
    "javascript:",
    "data:",
    "vbscript:",
    "java\tscript:",
    "java\nscript:",
    "java\rscript:",
)
_RE_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _clean_url_html(url: str) -> str:
    if not url:
        return ""

    decoded = unescape(url) if "&" in url else url
    cleaned = _RE_CONTROL_CHARS.sub("", decoded)
    return cleaned.strip().lower()


def _is_dangerous_protocol_html(clean_url: str, allow_data_images: bool) -> bool:
    if allow_data_images and clean_url.startswith("data:image/"):
        return False

    return clean_url.startswith(_DANGEROUS_PROTOCOLS)


def _is_safe_url(url: str, allow_data_images: bool = False) -> bool:
    """Check if URL is safe (no javascript:, etc.)."""
    if not url:
        return False

    clean_url = _clean_url_html(url)

    if _is_dangerous_protocol_html(clean_url, allow_data_images):
        return False

    return True
