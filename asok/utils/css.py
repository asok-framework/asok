from __future__ import annotations

import re


def _prefix_css_selector(part: str, prefix: str, global_marker: str) -> str:
    """Prefix a single CSS selector with the page scope, handling special cases."""
    if part.startswith(global_marker):
        return part.replace(global_marker, "")
    if part in ("html", "body"):
        return f"{part}{prefix}"
    return f"{prefix} {part}"


def _scope_selector_block(selector_text: str, prefix: str, global_marker: str) -> str:
    """Scope a comma-separated selector list with the given prefix."""
    prefixed_parts = []
    for part in selector_text.split(","):
        part = part.strip()
        if not part:
            continue
        # Skip keyframe selectors (0%, 100%, from, to)
        if part in ("from", "to") or re.match(r"^\d+%$", part):
            prefixed_parts.append(part)
            continue
        prefixed_parts.append(_prefix_css_selector(part, prefix, global_marker))
    return " " + ", ".join(prefixed_parts) + " "


def _is_invalid_for_scoping(content: str, page_id: str) -> bool:
    if not content:
        return True
    if len(content) > 1_000_000:
        return True
    if not page_id:
        return True
    if len(page_id) > 100:
        return True
    return False


def _is_selector_token(i: int, tokens: list[str]) -> bool:
    return i + 1 < len(tokens) and tokens[i + 1] == "{"


def _should_scope_selector(selector_text: str) -> bool:
    if not selector_text:
        return False
    if selector_text.startswith("@"):
        return False
    return True


def _process_css_tokens(tokens: list[str], prefix: str, global_marker: str) -> str:
    result = []
    for i, t in enumerate(tokens):
        if _is_selector_token(i, tokens):
            selector_text = t.strip()
            if _should_scope_selector(selector_text):
                result.append(_scope_selector_block(selector_text, prefix, global_marker))
            else:
                result.append(t)
        else:
            result.append(t)
    return "".join(result)


def scope_css(content: str, page_id: str) -> str:
    """Scope CSS content by prefixing selectors with [data-page-id='ID'].
    Supports :global(.class) to opt-out of scoping.

    Args:
        content: The raw CSS string to scope.
        page_id: The unique identifier for the page.

    Returns:
        The scoped CSS string.

    SECURITY: Size limits prevent DoS via extremely large CSS.
    """
    if _is_invalid_for_scoping(content, page_id):
        return content or ""

    prefix = f'[data-page-id="{page_id}"]'
    global_marker = "___GLOBAL___"

    # 1. Protect globals: :global(.selector)
    content = re.sub(
        r":global\s*\((.*?)\)", lambda m: f"{global_marker}{m.group(1)}", content
    )

    # 2. Process selectors using a stateful split on { and }
    tokens = re.split(r"({|})", content)
    output = _process_css_tokens(tokens, prefix, global_marker)

    # Final cleanup of any lingering global markers
    return output.replace(global_marker, "")
