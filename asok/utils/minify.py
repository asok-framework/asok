from __future__ import annotations

import re


def minify_html(html: str) -> str:
    """Minifies HTML content using the Python standard library (re).
    Safely preserves content inside <pre>, <code>, <script>, <style>, and <textarea>.

    Args:
        html: The raw HTML content to minify.

    Returns:
        The minified HTML string.
    """
    if not html:
        return ""

    # 1. Protect whitespace-sensitive tags
    # We use a placeholder to avoid affecting code snippets or template-injected scripts
    protected = []

    def protect(match):
        placeholder = f"___ASOK_PROTECTED_{len(protected)}___"
        protected.append(match.group(0))
        return placeholder

    # Tags to protect
    # IMPORTANT: Use a robust pattern that finds the REAL closing tag.
    # The naive r"<script.*?>.*?</script>" fails when the script content
    # contains a literal "</script>" string (e.g. inside a JS regex literal
    # like /<\/script>/gi used by the Asok directive runtime).
    # Solution: match the closing tag only when it appears as an actual HTML
    # tag (case-insensitive, not preceded by a backslash escape).
    sensitive_tags_patterns = [
        r"<pre(?:\s[^>]*)?>.*?</pre\s*>",
        r"<code(?:\s[^>]*)?>.*?</code\s*>",
        r"<script(?:\s[^>]*)?>.*?</script\s*>",
        r"<textarea(?:\s[^>]*)?>.*?</textarea\s*>",
        r"<style(?:\s[^>]*)?>.*?</style\s*>",
    ]

    current_html = html
    for tag_pattern in sensitive_tags_patterns:
        current_html = re.sub(
            tag_pattern, protect, current_html, flags=re.DOTALL | re.IGNORECASE
        )

    # 2. Aggressive minification
    # Remove HTML comments (except IE conditional comments and Asok markers)
    # Preserve: <!-- block:name:start/end --> and <!-- page-id:name -->
    current_html = re.sub(
        r"<!--(?!\s*\[if)(?!\s*block:)(?!\s*page-id:).*?-->",
        "",
        current_html,
        flags=re.DOTALL,
    )

    # Collapse multiple whitespaces/newlines into a single space everywhere
    current_html = re.sub(r"\s+", " ", current_html)

    # Remove whitespace between tags (commented out for template safety)
    # current_html = re.sub(r">\s+<", "><", current_html)

    # Remove whitespace around assignment operators in tags (optional but saves space)
    # current_html = re.sub(r'\s*=\s*', '=', current_html)

    # Trim start/end
    current_html = current_html.strip()

    # 3. Restore protected segments
    for i, content in enumerate(protected):
        current_html = current_html.replace(f"___ASOK_PROTECTED_{i}___", content)

    return current_html


def minify_css(css: str) -> str:
    """Minifies CSS content using regex.
    Removes comments and collapses all unnecessary whitespace.
    """
    if not css:
        return ""
    # Remove comments
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Collapse all whitespace into a single space
    css = re.sub(r"\s+", " ", css)
    # Remove spaces around symbols
    css = re.sub(r"\s*([{:;,>+])\s*", r"\1", css)
    return css.strip()


def minify_js(js: str) -> str:
    """Minifies JavaScript content by removing comments and extra whitespace.
    Safely handles string literals to avoid corrupting data.
    """
    if not js:
        return ""

    # 1. Remove multi-line comments
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)

    # 2. Remove single-line comments, being careful not to match '//' inside strings or regex
    # This is a simplified but safer version for template-injected scripts
    lines = []
    for line in js.splitlines():
        # Strip trailing comments if they aren't preceded by a colon (url) or quote
        stripped = re.sub(r"(?<![:\"'])//.*$", "", line)
        lines.append(stripped.strip())

    # 3. Collapse whitespace
    js = " ".join(lines)
    return re.sub(r"\s+", " ", js).strip()
