from __future__ import annotations

import hashlib
from typing import Any, Iterator, Optional

from ._preprocess_helpers import find_block_close
from .compiler import _compile_and_run
from .preprocessor import _RE_BLOCK_CLOSE, _RE_BLOCK_OPEN, _preprocess

# Global cache of preprocessed templates:
# (hash(template_string), strip_blocks, inject_markers) -> (preprocessed_string, macro_dict)
_preprocessed_cache: dict[tuple[int, bool, bool], tuple[str, dict[str, Any], str]] = {}


def clear_template_preprocessor_cache() -> None:
    """Clear all template preprocessor caches."""
    _preprocessed_cache.clear()


def _check_debug(context: dict[str, Any]) -> bool:
    if "DEBUG" in context:
        return context["DEBUG"]
    req = context.get("request")
    if req is None:
        return False
    if hasattr(req, "_debug_cached"):
        return req._debug_cached
    return _resolve_debug_uncached(req)


def _resolve_debug_uncached(req: Any) -> bool:
    environ = getattr(req, "environ", None)
    if environ is None:
        return False
    app = environ.get("asok.app")
    debug = app.config.get("DEBUG", False) if app else False
    try:
        req._debug_cached = debug
    except AttributeError:
        pass
    return debug


def _get_preprocessed_template(
    template_string: str,
    context: dict[str, Any],
    root_dir: Optional[str],
    strip_blocks: bool,
    inject_markers: bool,
    is_debug: bool,
) -> tuple[str, Optional[str]]:
    cache_key = (hash(template_string), strip_blocks, inject_markers)
    if not is_debug and cache_key in _preprocessed_cache:
        preprocessed, macros, md5_key = _preprocessed_cache[cache_key]
        context.update(macros)
        return preprocessed, md5_key

    keys_before = set(context.keys())
    preprocessed = _preprocess(
        template_string,
        context,
        root_dir,
        strip_blocks=strip_blocks,
        inject_markers=inject_markers,
    )
    md5_key = None
    if not is_debug:
        added_macros = {k: context[k] for k in set(context.keys()) - keys_before}
        md5_key = hashlib.md5(preprocessed.encode()).hexdigest()
        _preprocessed_cache[cache_key] = (preprocessed, added_macros, md5_key)
    return preprocessed, md5_key


def render_block_string(
    template_string: str,
    block_name: str,
    context: dict[str, Any],
    root_dir: Optional[str] = None,
) -> str:
    """Render only a specific named block from a template string.

    Useful for HTMX-style partial updates where only a specific fragment of
    the page is needed.
    """
    is_debug = _check_debug(context)
    preprocessed, md5_key = _get_preprocessed_template(
        template_string,
        context,
        root_dir,
        strip_blocks=False,
        inject_markers=False,
        is_debug=is_debug,
    )
    body = _extract_named_block(preprocessed, block_name)
    if body is None:
        raise ValueError(f"Block '{block_name}' not found in template")
    res = _compile_and_run(body, context, is_debug)
    return "".join(res) if res is not None else ""


def _extract_named_block(template_string: str, block_name: str) -> Optional[str]:
    for open_match in _RE_BLOCK_OPEN.finditer(template_string):
        if open_match.group(1) != block_name:
            continue
        close = find_block_close(
            template_string, open_match.end(), _RE_BLOCK_OPEN, _RE_BLOCK_CLOSE
        )
        if close:
            content_end, _ = close
            return template_string[open_match.end():content_end]
    return None


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
    is_debug = _check_debug(context)
    preprocessed, md5_key = _get_preprocessed_template(
        template_string,
        context,
        root_dir,
        strip_blocks=not inject_block_markers,
        inject_markers=inject_block_markers,
        is_debug=is_debug,
    )
    res = _compile_and_run(preprocessed, context, is_debug, cache_key=md5_key)
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
    is_debug = _check_debug(context)
    preprocessed, md5_key = _get_preprocessed_template(
        template_string,
        context,
        root_dir,
        strip_blocks=not inject_block_markers,
        inject_markers=inject_block_markers,
        is_debug=is_debug,
    )
    return _compile_and_run(preprocessed, context, is_debug, cache_key=md5_key)
