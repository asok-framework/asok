from __future__ import annotations

from typing import Any, Iterator, Optional

from .compiler import _compile_and_run
from .preprocessor import _RE_BLOCK_CLOSE, _RE_BLOCK_OPEN, _preprocess


def render_block_string(
    template_string: str,
    block_name: str,
    context: dict[str, Any],
    root_dir: Optional[str] = None,
) -> str:
    """Render only a specific named block from a template string.

    Useful for HTMX-style partial updates where only a specific fragment of the page is needed.
    """
    template_string = _preprocess(
        template_string, context, root_dir, strip_blocks=False
    )

    # Find the named block (nesting-aware)
    for open_match in _RE_BLOCK_OPEN.finditer(template_string):
        if open_match.group(1) != block_name:
            continue
        # Walk forward from after the opening tag, tracking nesting depth
        start = open_match.end()
        depth = 1
        pos = start
        while depth > 0:
            next_open = _RE_BLOCK_OPEN.search(template_string, pos)
            next_close = _RE_BLOCK_CLOSE.search(template_string, pos)
            if next_close is None:
                break
            if next_open and next_open.start() < next_close.start():
                depth += 1
                pos = next_open.end()
            else:
                depth -= 1
                if depth == 0:
                    res = _compile_and_run(
                        template_string[start : next_close.start()], context
                    )
                    return "".join(res) if res is not None else ""
                pos = next_close.end()

    raise ValueError(f"Block '{block_name}' not found in template")


def render_template_string(
    template_string: str,
    context: dict[str, Any],
    root_dir: Optional[str] = None,
    inject_block_markers: bool = False,
) -> str:
    """Compile and render a template string with the provided context.

    Args:
        inject_block_markers: If True, injects HTML comment markers around blocks
                             for data-block targeting without IDs
    """
    is_debug = False
    req = context.get("request")
    if req:
        app = getattr(req, "environ", {}).get("asok.app")
        if app:
            is_debug = app.config.get("DEBUG", False)

    template_string = _preprocess(
        template_string,
        context,
        root_dir,
        strip_blocks=not inject_block_markers,
        inject_markers=inject_block_markers,
    )
    res = _compile_and_run(template_string, context, is_debug)
    return "".join(res) if res is not None else ""


def stream_template_string(
    template_string: str,
    context: dict[str, Any],
    root_dir: Optional[str] = None,
    inject_block_markers: bool = False,
) -> Iterator[str]:
    """Compile and stream a template string, yielding results as they are generated.

    Args:
        inject_block_markers: If True, injects HTML comment markers around blocks
                             for data-block targeting without IDs
    """
    template_string = _preprocess(
        template_string,
        context,
        root_dir,
        strip_blocks=not inject_block_markers,
        inject_markers=inject_block_markers,
    )
    return _compile_and_run(template_string, context)
