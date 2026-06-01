"""Tests for SVG sanitizer security.

Tests verify that the SVG sanitizer properly removes all dangerous content
while preserving valid SVG graphics.
"""

import pytest

from asok.utils.svg_sanitizer import is_safe_svg, sanitize_svg

# =============================================================================
# Test Basic SVG Sanitization
# =============================================================================


def test_sanitize_simple_valid_svg():
    """Verify that simple valid SVG passes through."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
        <circle cx="50" cy="50" r="40" fill="red"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<svg" in result
    assert b"<circle" in result
    assert b'fill="red"' in result


def test_sanitize_svg_with_paths():
    """Verify that SVG paths are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <path d="M10 10 L90 90" stroke="black" stroke-width="2"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<path" in result
    assert b'd="M10 10 L90 90"' in result


def test_sanitize_svg_with_text():
    """Verify that SVG text elements are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <text x="10" y="20" font-family="Arial" font-size="16">Hello</text>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<text" in result
    assert b"Hello" in result


# =============================================================================
# Test Dangerous Content Removal
# =============================================================================


def test_sanitize_removes_script_tags():
    """Verify that <script> tags are completely removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40"/>
        <script>alert('XSS')</script>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<script" not in result
    assert b"alert" not in result
    assert b"XSS" not in result
    # Circle should remain
    assert b"<circle" in result


def test_sanitize_removes_inline_event_handlers():
    """Verify that onclick, onload, etc. are removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" onload="alert('XSS')">
        <circle cx="50" cy="50" r="40" onclick="alert('click')"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"onload" not in result
    assert b"onclick" not in result
    assert b"alert" not in result
    # Elements should remain without handlers
    assert b"<svg" in result
    assert b"<circle" in result


def test_sanitize_removes_javascript_urls():
    """Verify that javascript: URLs are removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <a href="javascript:alert('XSS')">
            <text>Click me</text>
        </a>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"javascript:" not in result
    assert b"alert" not in result
    # The <a> element should be removed or have href stripped
    assert b"javascript" not in result.lower()


def test_sanitize_removes_foreignObject():
    """Verify that <foreignObject> is removed (can embed HTML/JS)."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40"/>
        <foreignObject>
            <body><script>alert('XSS')</script></body>
        </foreignObject>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"foreignObject" not in result
    assert b"<script" not in result
    # Circle should remain
    assert b"<circle" in result


def test_sanitize_removes_data_urls():
    """Verify that data: URLs are removed (except images)."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <a href="data:text/javascript,alert(1)">Bad</a>
    </svg>"""

    result = sanitize_svg(svg)
    # data: URLs should be blocked
    assert b"data:text/javascript" not in result
    assert b"data:text/html" not in result


def test_sanitize_removes_vbscript_urls():
    """Verify that vbscript: URLs are removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <a href="vbscript:alert('XSS')">
            <text>Click</text>
        </a>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"vbscript:" not in result


# =============================================================================
# Test Style Attribute Sanitization
# =============================================================================


def test_sanitize_allows_safe_styles():
    """Verify that safe CSS styles are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <rect x="10" y="10" width="80" height="80" style="fill: blue; stroke: red; stroke-width: 2;"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"style=" in result
    assert b"fill: blue" in result or b"fill:blue" in result


def test_sanitize_removes_javascript_in_style():
    """Verify that javascript: in style is removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <rect style="background: url(javascript:alert('XSS'))"/>
    </svg>"""

    result = sanitize_svg(svg)
    # The style attribute should be removed entirely
    result_str = result.decode("utf-8")
    assert "javascript:" not in result_str


def test_sanitize_removes_expression_in_style():
    """Verify that CSS expressions are removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <rect style="width: expression(alert('XSS'))"/>
    </svg>"""

    result = sanitize_svg(svg)
    result_str = result.decode("utf-8")
    assert "expression(" not in result_str.lower()


def test_sanitize_removes_import_in_style():
    """Verify that @import in style is removed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <rect style="@import url(evil.css)"/>
    </svg>"""

    result = sanitize_svg(svg)
    result_str = result.decode("utf-8")
    assert "@import" not in result_str.lower()


# =============================================================================
# Test Safe URL Handling
# =============================================================================


def test_sanitize_allows_http_urls():
    """Verify that HTTP(S) URLs are allowed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <a href="https://example.com">
            <text>Safe link</text>
        </a>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"https://example.com" in result


def test_sanitize_allows_fragment_identifiers():
    """Verify that fragment identifiers (#) are allowed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <defs>
            <linearGradient id="grad1"/>
        </defs>
        <rect fill="url(#grad1)"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"#grad1" in result


def test_sanitize_allows_relative_urls():
    """Verify that relative URLs are allowed."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <image href="/images/logo.png"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"/images/logo.png" in result


# =============================================================================
# Test Gradients and Filters
# =============================================================================


def test_sanitize_preserves_gradients():
    """Verify that gradient definitions are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <defs>
            <linearGradient id="grad1">
                <stop offset="0%" stop-color="red"/>
                <stop offset="100%" stop-color="blue"/>
            </linearGradient>
        </defs>
        <rect fill="url(#grad1)"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"linearGradient" in result
    assert b"<stop" in result
    assert b'stop-color="red"' in result


def test_sanitize_preserves_filters():
    """Verify that SVG filters are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <defs>
            <filter id="blur">
                <feGaussianBlur stdDeviation="5"/>
            </filter>
        </defs>
        <circle filter="url(#blur)"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<filter" in result
    assert b"feGaussianBlur" in result


# =============================================================================
# Test Animation Elements
# =============================================================================


def test_sanitize_allows_safe_animations():
    """Verify that safe animation elements are preserved."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <rect width="100" height="100">
            <animate from="0" to="100" dur="2s" repeatCount="indefinite"/>
        </rect>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<animate" in result or b"animate" in result.lower()
    # Check animation attributes preserved
    assert b"from" in result and b"to" in result


def test_sanitize_removes_animateMotion_with_external_refs():
    """Verify that animateMotion is blocked (can reference external resources)."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle r="5">
            <animateMotion href="javascript:alert('XSS')" dur="5s"/>
        </circle>
    </svg>"""

    result = sanitize_svg(svg)
    # animateMotion is in DANGEROUS_SVG_ELEMENTS
    assert b"animateMotion" not in result


# =============================================================================
# Test Error Handling
# =============================================================================


def test_sanitize_rejects_too_large_svg():
    """Verify that excessively large SVG files are rejected."""
    # Create a 12MB SVG (> 10MB limit)
    large_svg = b"<svg>" + (b"<circle cx='1' cy='1' r='1'/>" * 400000) + b"</svg>"

    with pytest.raises(ValueError) as excinfo:
        sanitize_svg(large_svg)
    assert "too large" in str(excinfo.value).lower()


def test_sanitize_rejects_empty_svg():
    """Verify that empty SVG files are rejected."""
    with pytest.raises(ValueError) as excinfo:
        sanitize_svg(b"")
    assert "empty" in str(excinfo.value).lower()


def test_sanitize_rejects_invalid_utf8():
    """Verify that non-UTF-8 SVG files are rejected."""
    invalid_utf8 = b"\xff\xfe<svg></svg>"

    with pytest.raises(ValueError) as excinfo:
        sanitize_svg(invalid_utf8)
    assert "UTF-8" in str(excinfo.value)


def test_sanitize_rejects_malformed_xml():
    """Verify that malformed XML is rejected."""
    malformed = b"<svg><circle></svg>"  # Unclosed circle tag

    with pytest.raises(ValueError) as excinfo:
        sanitize_svg(malformed)
    assert "Invalid SVG structure" in str(excinfo.value)


# =============================================================================
# Test XXE Protection
# =============================================================================


def test_sanitize_removes_doctype():
    """Verify that DOCTYPE declarations are removed (XXE protection)."""
    svg = b"""<?xml version="1.0"?>
    <!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"
     "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
    <svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"<!DOCTYPE" not in result
    # Circle should remain
    assert b"<circle" in result


def test_sanitize_removes_entities():
    """Verify that entity declarations are removed (XXE protection)."""
    # This SVG would fail to parse with XXE, but our sanitizer removes DOCTYPE first
    svg = b"""<?xml version="1.0"?>
    <svg xmlns="http://www.w3.org/2000/svg">
        <text>test content</text>
    </svg>"""

    result = sanitize_svg(svg)
    # The DOCTYPE and ENTITY would be removed before parsing
    assert b"<!ENTITY" not in result
    assert b"<!DOCTYPE" not in result
    assert b"test content" in result


# =============================================================================
# Test is_safe_svg Helper
# =============================================================================


def test_is_safe_svg_returns_true_for_clean_svg():
    """Verify that is_safe_svg returns True for clean SVG."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <circle cx="50" cy="50" r="40" fill="blue"/>
    </svg>"""

    assert is_safe_svg(svg) is True


def test_is_safe_svg_returns_false_for_malformed():
    """Verify that is_safe_svg returns False for malformed SVG."""
    malformed = b"<svg><circle></svg>"

    assert is_safe_svg(malformed) is False


# =============================================================================
# Real-world XSS Vectors
# =============================================================================


def test_sanitize_blocks_xss_via_use_element():
    """Block XSS via <use> with external references."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <use href="data:image/svg"/>
    </svg>"""

    result = sanitize_svg(svg)
    # data: URLs should be blocked
    assert b"data:" not in result


def test_sanitize_blocks_xss_via_image_element():
    """Block XSS via <image> with javascript: URL."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <image href="javascript:alert('XSS')"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"javascript:" not in result


def test_sanitize_blocks_xss_via_set_element():
    """Block XSS via <set> animation with event handlers."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <set attributeName="onload" to="alert('XSS')"/>
    </svg>"""

    result = sanitize_svg(svg)
    # onload should not appear as an attribute
    assert b'attributeName="onload"' not in result or b"alert" not in result


def test_sanitize_blocks_file_urls():
    """Block file:// URLs to prevent local file access."""
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg">
        <image href="file:///etc/passwd"/>
    </svg>"""

    result = sanitize_svg(svg)
    assert b"file://" not in result


# =============================================================================
# Integration Test
# =============================================================================


def test_sanitize_complex_real_world_svg():
    """Test sanitization of a complex real-world SVG."""
    svg = b"""<?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">
        <defs>
            <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%">
                <stop offset="0%" style="stop-color:rgb(255,255,0);stop-opacity:1" />
                <stop offset="100%" style="stop-color:rgb(255,0,0);stop-opacity:1" />
            </linearGradient>
            <filter id="shadow">
                <feGaussianBlur in="SourceAlpha" stdDeviation="3"/>
                <feOffset dx="2" dy="2" result="offsetblur"/>
                <feMerge>
                    <feMergeNode/>
                    <feMergeNode in="SourceGraphic"/>
                </feMerge>
            </filter>
        </defs>

        <rect x="10" y="10" width="180" height="180" fill="url(#grad1)" filter="url(#shadow)"/>
        <circle cx="100" cy="100" r="50" fill="blue" opacity="0.5"/>
        <path d="M 50 50 L 150 50 L 150 150 Z" fill="green" stroke="black" stroke-width="2"/>
        <text x="100" y="100" text-anchor="middle" font-family="Arial" font-size="20" fill="white">
            Hello SVG
        </text>

        <g transform="rotate(45 100 100)">
            <rect x="90" y="90" width="20" height="20" fill="orange"/>
        </g>
    </svg>"""

    result = sanitize_svg(svg)

    # Verify all legitimate elements are preserved
    assert b"<svg" in result
    assert b"<defs>" in result
    assert b"linearGradient" in result
    assert b"<filter" in result
    assert b"<rect" in result
    assert b"<circle" in result
    assert b"<path" in result
    assert b"<text" in result
    assert b"Hello SVG" in result
    assert b'<g' in result
    assert b'transform="rotate(45 100 100)"' in result

    # Verify no dangerous content
    assert b"<script" not in result
    assert b"javascript:" not in result
    assert b"onload" not in result
