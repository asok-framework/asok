from __future__ import annotations


def scope_js(content: str) -> str:
    """Wrap JS content in an IIFE for page-level scoping.

    Args:
        content: The raw JavaScript string to wrap.

    Returns:
        The scoped JavaScript content.
    """
    if not content:
        return ""
    # Avoid double wrapping if already looks like an IIFE
    trimmed = content.strip()
    if trimmed.startswith("(function") and trimmed.endswith("})();"):
        return content
    return f"(function(){{\n{content}\n}})();"
