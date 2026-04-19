from __future__ import annotations

import re


def scope_css(content: str, page_id: str) -> str:
    """Scope CSS content by prefixing selectors with [data-asok-page='ID'].
    Supports :global(.class) to opt-out of scoping.

    Args:
        content: The raw CSS string to scope.
        page_id: The unique identifier for the page.

    Returns:
        The scoped CSS string.
    """
    if not content:
        return ""

    prefix = f'[data-asok-page="{page_id}"]'
    global_marker = "___GLOBAL___"

    # 1. Protect globals
    # Matches :global(.selector)
    content = re.sub(
        r":global\s*\((.*?)\)", lambda m: f"{global_marker}{m.group(1)}", content
    )

    # 2. Process selectors
    # We use a stateful-like split to find selectors (text before {)
    # This handles @media blocks because they also follow the 'text before {' pattern
    tokens = re.split(r"({|})", content)
    result = []

    for i in range(len(tokens)):
        t = tokens[i]

        # If this token is followed by a '{', it's a selector or an @-rule
        if i + 1 < len(tokens) and tokens[i + 1] == "{":
            selector_text = t.strip()

            if not selector_text or selector_text.startswith("@"):
                # Pass @media, @keyframes, etc. through as-is
                # Their internal contents will be processed in subsequent iterations
                result.append(t)
            else:
                # It's a list of selectors to prefix
                prefixed_parts = []
                for part in selector_text.split(","):
                    part = part.strip()
                    if not part:
                        continue

                    # Skip keyframe selectors (0%, 100%, from, to)
                    if part in ["from", "to"] or re.match(r"^\d+%$", part):
                        prefixed_parts.append(part)
                        continue

                    if part.startswith(global_marker):
                        # Unwrap :global
                        prefixed_parts.append(part.replace(global_marker, ""))
                    elif part in ["html", "body"]:
                        # body -> body[data-asok-page="id"]
                        prefixed_parts.append(f"{part}{prefix}")
                    else:
                        # .class -> [data-asok-page="id"] .class
                        prefixed_parts.append(f"{prefix} {part}")

                result.append(" " + ", ".join(prefixed_parts) + " ")
        else:
            result.append(t)

    output = "".join(result)
    # Final cleanup of any lingering global markers
    return output.replace(global_marker, "")
