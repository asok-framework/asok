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
    sensitive_tags_patterns = [
        r"<pre.*?>.*?</pre>",
        r"<code.*?>.*?</code>",
        r"<script.*?>.*?</script>",
        r"<textarea.*?>.*?</textarea>",
        r"<style.*?>.*?</style>",
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
        flags=re.DOTALL
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
    """Safe but effective JS minification.
    Removes comments and collapses whitespace/newlines.
    """
    if not js:
        return ""
    # Remove multi-line comments
    js = re.sub(r"/\*.*?\*/", "", js, flags=re.DOTALL)
    # Remove single-line comments safely
    js = re.sub(r"(^|[^\\])//.*?\n", r"\1\n", js)
    # Collapse multiple spaces
    js = re.sub(r"[ \t]+", " ", js)
    # Collapse newlines around symbols where safe
    js = re.sub(r"\s*([{}()=;,:])\s*", r"\1", js)
    # Remove redundant newlines
    js = re.sub(r"\n+", "\n", js)
    return js.strip()
