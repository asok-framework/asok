"""HTML sanitizer for WYSIWYG content.

SECURITY: Prevents Stored XSS attacks by sanitizing user-generated HTML.
"""

import re
from html import escape

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
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "p": {"style"},
    "span": {"style"},
    "div": {"style"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

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


def sanitize_html(html: str) -> str:
    """Sanitize HTML content to prevent XSS attacks.

    This is a lightweight sanitizer for WYSIWYG editor content.
    For production use with untrusted content, consider using bleach library.

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
        - Size limits prevent DoS via extremely large HTML
    """
    if not html:
        return ""

    # SECURITY: Reject excessively large HTML to prevent DoS (max 1MB for WYSIWYG)
    if len(html) > 1_000_000:
        return escape(html[:1000]) + "... [content too large]"

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

    # Parse and rebuild HTML with whitelist
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
        # Add text before tag
        text_before = html[pos : match.start()]
        if text_before:
            result.append(escape(text_before))

        closing = match.group(1)
        tag = match.group(2).lower()
        attrs = match.group(3)

        # Check if tag is allowed
        if tag in ALLOWED_TAGS:
            # Closing tag
            if closing:
                result.append(f"</{tag}>")
            else:
                # Opening tag - sanitize attributes
                sanitized_attrs = _sanitize_attributes(tag, attrs)
                if sanitized_attrs:
                    result.append(f"<{tag} {sanitized_attrs}>")
                else:
                    result.append(f"<{tag}>")
        # else: skip unknown tags

        pos = match.end()

    # Add remaining text
    if pos < len(html):
        result.append(escape(html[pos:]))

    return "".join(result)


def _sanitize_attributes(tag: str, attrs: str) -> str:
    """Sanitize HTML attributes for a given tag.

    SECURITY: Improved parser handles both quoted and unquoted attributes.
    """
    if not attrs or tag not in ALLOWED_ATTRIBUTES:
        return ""

    allowed = ALLOWED_ATTRIBUTES[tag]
    sanitized = []

    # SECURITY: Enhanced regex to match both quoted and unquoted attributes
    # Matches: name="value", name='value', name=value
    attr_pattern = re.compile(
        r'(\w+)\s*=\s*(?:(["\'])((?:(?!\2).)*)\2|([^\s>]+))', re.IGNORECASE
    )

    for match in attr_pattern.finditer(attrs):
        attr_name = match.group(1).lower()
        # Get value from either quoted (group 3) or unquoted (group 4)
        attr_value = match.group(3) if match.group(3) is not None else match.group(4)

        if attr_name in allowed:
            # Special handling for style attribute
            if attr_name == "style":
                attr_value = _sanitize_style(attr_value)
                if not attr_value:
                    continue
            # Special handling for href
            elif attr_name == "href":
                if not _is_safe_url(attr_value):
                    continue
            # Special handling for src
            elif attr_name == "src":
                if not _is_safe_url(attr_value, allow_data_images=True):
                    continue

            # Escape attribute value
            sanitized.append(f'{attr_name}="{escape(attr_value, quote=True)}"')

    return " ".join(sanitized)


def _sanitize_style(style: str) -> str:
    """Sanitize CSS style attribute."""
    if not style:
        return ""

    # Parse style declarations
    safe_rules = []
    for rule in style.split(";"):
        rule = rule.strip()
        if ":" not in rule:
            continue

        prop, value = rule.split(":", 1)
        prop = prop.strip().lower()
        value = value.strip()

        # Check if property is allowed
        if prop in ALLOWED_STYLES:
            # Remove dangerous values
            if re.search(r"expression|javascript|import|url\(", value, re.IGNORECASE):
                continue
            # Don't escape the value here, just validate it
            safe_rules.append(f"{prop}: {value}")

    return "; ".join(safe_rules) if safe_rules else ""


def _is_safe_url(url: str, allow_data_images: bool = False) -> bool:
    """Check if URL is safe (no javascript:, etc.)."""
    if not url:
        return False

    # Remove ASCII control characters (ordinals < 32 and 127) which browsers ignore/strip in URLs
    clean_url = (
        "".join(c for c in url if ord(c) >= 32 and ord(c) != 127).strip().lower()
    )

    # Block dangerous protocols
    if any(
        clean_url.startswith(proto) for proto in ["javascript:", "data:", "vbscript:"]
    ):
        # Allow data:image/* if specified
        if allow_data_images and clean_url.startswith("data:image/"):
            return True
        return False

    return True
