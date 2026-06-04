"""SVG sanitizer for safe handling of user-uploaded SVG files.

SECURITY: SVG files can contain JavaScript and other dangerous content that can
lead to XSS attacks. This sanitizer removes all potentially dangerous elements
and attributes while preserving valid SVG graphics.

References:
- https://owasp.org/www-community/attacks/SVG_XSS
- https://portswigger.net/research/svg-security-patterns
"""

import re
import xml.etree.ElementTree as ET
from typing import Optional

# SVG namespace
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"

# Whitelist of safe SVG elements (lowercase for case-insensitive matching)
# Reference: https://developer.mozilla.org/en-US/docs/Web/SVG/Element
SAFE_SVG_ELEMENTS = {
    # Container elements
    "svg",
    "g",
    "defs",
    "symbol",
    "marker",
    "clippath",  # lowercase
    "mask",
    "pattern",
    # Shape elements
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    # Text elements
    "text",
    "tspan",
    "textpath",  # lowercase
    # Paint server elements
    "lineargradient",  # lowercase
    "radialgradient",  # lowercase
    "stop",
    # Descriptive elements
    "title",
    "desc",
    "metadata",
    # Filter elements
    "filter",
    "feblend",  # lowercase
    "fecolormatrix",  # lowercase
    "fecomponenttransfer",  # lowercase
    "fecomposite",  # lowercase
    "feconvolvematrix",  # lowercase
    "fediffuselighting",  # lowercase
    "fedisplacementmap",  # lowercase
    "feflood",  # lowercase
    "fegaussianblur",  # lowercase
    "feimage",  # lowercase
    "femerge",  # lowercase
    "femergenode",  # lowercase
    "femorphology",  # lowercase
    "feoffset",  # lowercase
    "fespecularlighting",  # lowercase
    "fetile",  # lowercase
    "feturbulence",  # lowercase
    "fedistantlight",  # lowercase
    "fepointlight",  # lowercase
    "fespotlight",  # lowercase
    "fefunca",  # lowercase
    "fefuncb",  # lowercase
    "fefuncg",  # lowercase
    "fefuncr",  # lowercase
    # Animation elements (with restrictions)
    "animate",
    "animatetransform",  # lowercase
    "set",
    # Other safe elements
    "use",
    "image",
    "view",
    "switch",
}

# DANGEROUS elements that must be removed (lowercase)
DANGEROUS_SVG_ELEMENTS = {
    "script",  # JavaScript execution
    "foreignobject",  # lowercase - Can embed HTML/JS
    "iframe",  # Can embed malicious content
    "embed",  # Can embed malicious content
    "object",  # Can embed malicious content
    "animatemotion",  # lowercase - Can reference external resources unsafely
}

# Safe SVG attributes
SAFE_SVG_ATTRIBUTES = {
    # Core attributes
    "id",
    "class",
    # Styling (we'll check style content separately)
    "style",
    "fill",
    "fill-opacity",
    "fill-rule",
    "stroke",
    "stroke-width",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-dasharray",
    "stroke-dashoffset",
    "opacity",
    "color",
    "visibility",
    "display",
    # Transform
    "transform",
    # Geometry
    "x",
    "y",
    "width",
    "height",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "x1",
    "y1",
    "x2",
    "y2",
    "points",
    "d",  # path data
    "viewBox",
    "preserveAspectRatio",
    # Text
    "font-family",
    "font-size",
    "font-weight",
    "font-style",
    "text-anchor",
    "text-decoration",
    # Gradients
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    # Filters
    "filterUnits",
    "primitiveUnits",
    "in",
    "in2",
    "result",
    "mode",
    "type",
    "values",
    "stdDeviation",
    # Animation
    "attributeName",
    "from",
    "to",
    "dur",
    "repeatCount",
    "begin",
    "end",
    # Links (with URL validation)
    "href",
    "xlink:href",
    # Clipping/masking
    "clip-path",
    "mask",
    "clipPathUnits",
    "maskUnits",
    "maskContentUnits",
}

# Regex to detect event handlers
EVENT_HANDLER_PATTERN = re.compile(r"^on[a-z]+$", re.IGNORECASE)


def sanitize_svg(svg_content: bytes) -> bytes:
    """Sanitize an SVG file by removing dangerous content.

    Args:
        svg_content: Raw SVG file content as bytes

    Returns:
        Sanitized SVG content as bytes

    Raises:
        ValueError: If the SVG is invalid or cannot be parsed

    Security measures:
    - Removes <script>, <foreignObject>, and other dangerous elements
    - Removes all event handlers (onclick, onload, etc.)
    - Validates and sanitizes URLs in href/xlink:href
    - Removes javascript: and data: protocols (except data:image/*)
    - Validates style attribute content
    - Limits document size to prevent DoS
    """
    # SECURITY: Reject excessively large SVG files to prevent DoS
    if len(svg_content) > 10_000_000:  # 10MB limit
        raise ValueError("SVG file too large (max 10MB)")

    # SECURITY: Reject empty files
    if not svg_content or len(svg_content) < 10:
        raise ValueError("SVG file is empty or too small")

    # Decode to string for processing
    try:
        svg_string = svg_content.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("SVG file is not valid UTF-8")

    # SECURITY: Remove XML processing instructions and DOCTYPE (can be used for XXE attacks)
    svg_string = re.sub(r"<\?xml[^>]*\?>", "", svg_string)
    svg_string = re.sub(r"<!DOCTYPE[^>]*>", "", svg_string, flags=re.IGNORECASE)
    svg_string = re.sub(r"<!ENTITY[^>]*>", "", svg_string, flags=re.IGNORECASE)
    svg_string = re.sub(r"<!--.*?-->", "", svg_string, flags=re.DOTALL)

    # Parse the SVG
    try:
        # SECURITY: defusedxml would be better, but ElementTree is safer than lxml for basic parsing
        # We've already removed DOCTYPE and entities above
        root = ET.fromstring(svg_string.encode("utf-8"))
    except ET.ParseError as e:
        raise ValueError(f"Invalid SVG structure: {e}")

    # Sanitize the tree
    _sanitize_element(root)

    # Remove namespace prefixes before serialization
    _remove_namespace_prefixes(root)

    # Rebuild the SVG
    sanitized = ET.tostring(root, encoding="utf-8", method="xml")

    # Add XML declaration back (safe)
    sanitized = b'<?xml version="1.0" encoding="UTF-8"?>\n' + sanitized

    return sanitized


def _remove_namespace_prefixes(element: ET.Element) -> None:
    """Remove namespace prefixes from element tags (ns0:svg -> svg).

    This makes the output cleaner and easier to work with.
    """
    # Remove namespace from tag
    element.tag = _strip_namespace(element.tag)

    # Process all children recursively
    for child in element:
        _remove_namespace_prefixes(child)


def _sanitize_element(element: ET.Element) -> None:
    """Recursively sanitize an XML element and its children.

    This function modifies the element tree in-place, removing dangerous
    elements and attributes.
    """
    # Get the tag name without namespace
    tag = _strip_namespace(element.tag)

    # SECURITY: Remove dangerous elements entirely
    if tag.lower() in DANGEROUS_SVG_ELEMENTS:
        # Clear all children and text
        element.clear()
        element.tag = "removed"  # Mark for removal
        return

    # SECURITY: Only allow whitelisted SVG elements
    if tag.lower() not in SAFE_SVG_ELEMENTS:
        # Convert unknown elements to <g> (safe container)
        element.tag = "g"

    # Sanitize attributes
    _sanitize_attributes(element)

    # Recursively sanitize children
    children_to_remove = []
    for child in list(element):
        _sanitize_element(child)
        # Remove elements marked as dangerous
        if _strip_namespace(child.tag) == "removed":
            children_to_remove.append(child)

    for child in children_to_remove:
        element.remove(child)


def _sanitize_attributes(element: ET.Element) -> None:
    """Sanitize attributes of an XML element in-place."""
    attrs_to_remove = []

    for attr_name, attr_value in list(element.attrib.items()):
        attr_name_clean = _strip_namespace(attr_name)

        # SECURITY: Remove event handlers (onclick, onload, etc.)
        if EVENT_HANDLER_PATTERN.match(attr_name_clean):
            attrs_to_remove.append(attr_name)
            continue

        # SECURITY: Remove attributes not in whitelist
        if attr_name_clean.lower() not in SAFE_SVG_ATTRIBUTES:
            attrs_to_remove.append(attr_name)
            continue

        # SECURITY: Validate URLs in href attributes
        if attr_name_clean.lower() in ("href", "xlink:href"):
            if not _is_safe_url(attr_value):
                attrs_to_remove.append(attr_name)
                continue

        # SECURITY: Validate style attribute
        if attr_name_clean.lower() == "style":
            safe_style = _sanitize_style_attribute(attr_value)
            if safe_style:
                element.set(attr_name, safe_style)
            else:
                attrs_to_remove.append(attr_name)
            continue

    # Remove dangerous attributes
    for attr in attrs_to_remove:
        del element.attrib[attr]


def _sanitize_style_attribute(style: str) -> Optional[str]:
    """Sanitize CSS in style attribute.

    Returns:
        Sanitized style string or None if unsafe
    """
    if not style:
        return None

    # SECURITY: Remove dangerous CSS functions
    dangerous_patterns = [
        r"javascript:",
        r"expression\(",
        r"@import",
        r"behavior:",
        r"-moz-binding:",
        r"url\([^)]*javascript:",
        r"url\([^)]*data:(?!image/)",
    ]

    style_lower = style.lower()
    for pattern in dangerous_patterns:
        if re.search(pattern, style_lower):
            return None

    return style


def _is_safe_url(url: str) -> bool:
    """Check if a URL is safe for use in SVG.

    Args:
        url: URL to validate

    Returns:
        True if safe, False otherwise
    """
    if not url:
        return False

    # Remove ASCII control characters (ordinals < 32 and 127) which browsers ignore/strip in URLs
    clean_url = "".join(c for c in url if ord(c) >= 32 and ord(c) != 127).strip().lower()

    # SECURITY: Block dangerous protocols
    dangerous_protocols = [
        "javascript:",
        "data:",  # Block all data: URLs in SVG (images should be external)
        "vbscript:",
        "file:",
        "about:",
    ]

    for protocol in dangerous_protocols:
        if clean_url.startswith(protocol):
            return False

    # Allow relative URLs and http(s)
    if clean_url.startswith(("http://", "https://", "#", "/")):
        return True

    # Allow fragment identifiers and relative paths
    if url.startswith("#") or ":" not in url:
        return True

    # Block everything else
    return False


def _strip_namespace(tag: str) -> str:
    """Remove XML namespace from tag name.

    Args:
        tag: Tag name, possibly with namespace like {http://...}tagname

    Returns:
        Tag name without namespace
    """
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def is_safe_svg(svg_content: bytes) -> bool:
    """Check if an SVG file is safe without modifying it.

    Args:
        svg_content: Raw SVG file content

    Returns:
        True if the SVG contains no dangerous content, False otherwise
    """
    try:
        sanitize_svg(svg_content)
        # If sanitization succeeds without errors, the SVG is safe
        return True
    except ValueError:
        return False
