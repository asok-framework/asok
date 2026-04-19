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

    # 2. Basic minification
    # Remove HTML comments (except IE conditional comments)
    current_html = re.sub(r"<!--(?!\s*\[if).*?-->", "", current_html, flags=re.DOTALL)

    # Collapse multiple whitespaces/newlines into a single space
    current_html = re.sub(r"\s+", " ", current_html)

    # Remove whitespace between tags (only if it's pure whitespace)
    current_html = re.sub(r">\s+<", "><", current_html)

    # Trim start/end
    current_html = current_html.strip()

    # 3. Restore protected segments
    for i, content in enumerate(protected):
        current_html = current_html.replace(f"___ASOK_PROTECTED_{i}___", content)

    return current_html
