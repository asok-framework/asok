from __future__ import annotations

import os
import re
from typing import Any, Optional

from ._preprocess_helpers import (
    escape_template_literal,
    extract_child_blocks,
    extract_child_orphans,
    find_block_close,
    is_inside_no_comment_tag,
    make_macro,
    parse_macro_params,
    read_template_file,
    safe_resolve,
    search_template_path,
    top_level_block_ranges,
)

# Pre-compiled regex patterns
_RE_EXTENDS = re.compile(r"{%-?\s*extends\s+[\'\"](.*?)[\'\"]\s*-?%}")
_RE_INCLUDE = re.compile(r"{%-?\s*include\s+(.*?)\s*-?%}")
_RE_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_RE_TOKENS = re.compile(r"(?s)({{.*?}}|{%.*?%})")
_RE_BLOCK_OPEN = re.compile(r"{%-?\s*block\s+(\w+)\s*-?%}")
_RE_BLOCK_CLOSE = re.compile(r"{%-?\s*endblock(?:\s+\w+)?\s*-?%}")
_RE_FROM_IMPORT = re.compile(r"{%-?\s*from\s+['\"](.+?)['\"]\s+import\s+(.+?)\s*-?%}")
_RE_IMPORT_AS = re.compile(r"{%-?\s*import\s+['\"](.+?)['\"]\s+as\s+(\w+)\s*-?%}")
_RE_FILTER_BLOCK = re.compile(
    r"{%-?\s*filter\s+(\w+(?:\([^)]*\))?)\s*-?%}(.*?){%-?\s*endfilter\s*-?%}",
    re.DOTALL,
)
_RE_AUTOESCAPE_BLOCK = re.compile(
    r"{%-?\s*autoescape\s+(true|false)\s*-?%}(.*?){%-?\s*endautoescape\s*-?%}",
    re.DOTALL,
)
_RE_MACRO = re.compile(
    r"{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%}(.*?){%-?\s*endmacro\s*-?%}", re.DOTALL
)
_RE_RAW = re.compile(r"{%-?\s*raw\s*-?%}(.*?){%-?\s*endraw\s*-?%}", re.DOTALL)
_RE_COMPONENT = re.compile(
    r"{%-?\s*component\s+[\'\"](.*?)[\'\"]\s*(.*?)-?%}(.*?){%-?\s*endcomponent\s*-?%}",
    re.DOTALL,
)
_RE_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_RE_HTML_COMMENT_TOKEN = re.compile(r"\x00ASOKHC(\d+)\x00")

_macro_cache: dict[str, str] = {}
_macro_mtimes: dict[str, float] = {}


def _mask_html_comments(text: str) -> tuple[str, list[str]]:
    """Replace each ``<!-- ... -->`` with a sentinel so the preprocessor's
    regexes for ``{% include %}``, ``{% extends %}``, ``{% from %}`` etc. do
    not match Jinja-looking syntax that lives inside an HTML comment. Without
    this, an example like ``<!-- example: {% include "x" %} -->`` would be
    treated as a real include and recursively expanded.
    """
    stash: list[str] = []

    def replace(m: re.Match[str]) -> str:
        stash.append(m.group(0))
        return f"\x00ASOKHC{len(stash) - 1}\x00"

    return _RE_HTML_COMMENT.sub(replace, text), stash


def _restore_html_comments(text: str, stash: list[str]) -> str:
    def replace(m: re.Match[str]) -> str:
        return stash[int(m.group(1))]

    return _RE_HTML_COMMENT_TOKEN.sub(replace, text)


def _safe_resolve(base: str, requested: str) -> str:
    """Ensure requested path resolves within base directory."""
    return safe_resolve(base, requested)


def _get_cached_macro_content(file_path: str) -> Optional[str]:
    """Read macro file content, populating the cache + mtime map.

    SECURITY: capped at 1 MB to prevent memory DoS.
    """
    if not os.path.exists(file_path):
        return None
    cached = _macro_cache.get(file_path)
    if cached is not None and not _macro_file_changed(file_path):
        return cached
    content = read_template_file(file_path)
    if content is None:
        return None
    _macro_cache[file_path] = content
    _macro_mtimes[file_path] = os.path.getmtime(file_path)
    return content


def _macro_file_changed(file_path: str) -> bool:
    try:
        current_mtime = os.path.getmtime(file_path)
    except OSError:
        return True
    cached_mtime = _macro_mtimes.get(file_path)
    return cached_mtime is None or current_mtime > cached_mtime


def _get_all_macro_names(file_path: str) -> list[str]:
    """Get all macro names from a file without loading them.

    SECURITY: File size limits prevent DoS via extremely large macro files.
    """
    content = _get_cached_macro_content(file_path)
    if content is None:
        return []
    return [m.group(1) for m in _RE_MACRO.finditer(content)]


def _extract_macros(
    file_path: str, names: list[str], parent_ctx: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Parse a macro file and return callables for the requested macro names.

    All macros in the file are made available to each other (sibling calls).
    SECURITY: File size limits prevent DoS via extremely large macro files.
    """
    content = _get_cached_macro_content(file_path)
    if content is None:
        return {}
    all_macros: dict[str, Any] = {}
    for match in _RE_MACRO.finditer(content):
        macro_name, body, raw_params = (
            match.group(1),
            match.group(3),
            match.group(2).strip(),
        )
        param_names, param_defaults, varargs, varkw = parse_macro_params(raw_params)
        all_macros[macro_name] = make_macro(
            body,
            param_names,
            param_defaults,
            varargs,
            varkw,
            sibling_lookup=all_macros,
            parent_ctx=parent_ctx,
        )
    return {n: all_macros[n] for n in names if n in all_macros}


# ── Inheritance pipeline ────────────────────────────────────────────


def _resolve_extends_path(text: str, root_dir) -> Optional[str]:
    extends_match = _RE_EXTENDS.search(text)
    if not extends_match:
        return None
    return search_template_path(root_dir, extends_match.group(1))


def _handle_inheritance(text: str, root_dir, depth: int = 0) -> str:
    if depth > 5:
        return text
    parent_path = _resolve_extends_path(text, root_dir)
    if parent_path is None:
        if _RE_EXTENDS.search(text):
            return (
                f"<!-- Inheritance Error: "
                f"{_RE_EXTENDS.search(text).group(1)} not found in search paths -->"
            )
        return text
    parent_text = read_template_file(parent_path)
    if parent_text is None:
        return "<!-- Inheritance Error: template file too large or unreadable -->"
    return _merge_parent_with_child(text, parent_text, root_dir, depth)


def _merge_parent_with_child(text: str, parent_text: str, root_dir, depth: int) -> str:
    child_logic = _collect_child_orphan_logic(text)
    child_blocks = extract_child_blocks(text, _RE_BLOCK_OPEN, _RE_BLOCK_CLOSE)
    parent_text = _splice_child_blocks_into_parent(parent_text, child_blocks)
    if child_logic:
        parent_text = child_logic + "\n" + parent_text
    return _handle_inheritance(parent_text, root_dir, depth + 1)


def _collect_child_orphan_logic(text: str) -> str:
    outside_text = ""
    last_pos = 0
    for start, end in sorted(
        top_level_block_ranges(text, _RE_BLOCK_OPEN, _RE_BLOCK_CLOSE)
    ):
        outside_text += text[last_pos:start]
        last_pos = end
    outside_text += text[last_pos:]
    return "\n".join(extract_child_orphans(outside_text, _RE_TOKENS))


def _splice_child_blocks_into_parent(
    parent_text: str, child_blocks: dict[str, str]
) -> str:
    replacements = _collect_parent_block_replacements(parent_text)
    for full_start, full_end, name, content_start, content_end in sorted(
        replacements, key=lambda x: x[0], reverse=True
    ):
        content = child_blocks.get(name, parent_text[content_start:content_end])
        replacement = f"{{% block {name} %}}{content}{{% endblock %}}"
        parent_text = parent_text[:full_start] + replacement + parent_text[full_end:]
    return parent_text


def _collect_parent_block_replacements(parent_text: str):
    out = []
    for m in _RE_BLOCK_OPEN.finditer(parent_text):
        name = m.group(1)
        start = m.end()
        close = find_block_close(parent_text, start, _RE_BLOCK_OPEN, _RE_BLOCK_CLOSE)
        if close:
            content_end, full_end = close
            out.append((m.start(), full_end, name, start, content_end))
    return out


# ── Block marker injection ──────────────────────────────────────────


_SKIP_MARKER_BLOCKS = {"title", "description", "styles", "scripts"}


def _inject_block_markers(template_string: str) -> str:
    replacements = []
    for open_match in _RE_BLOCK_OPEN.finditer(template_string):
        block_name = open_match.group(1)
        close = find_block_close(
            template_string, open_match.end(), _RE_BLOCK_OPEN, _RE_BLOCK_CLOSE
        )
        if close is None:
            continue
        _, close_end = close
        if not _should_inject_block_marker(
            template_string, open_match.start(), block_name
        ):
            continue
        replacements.append((open_match.start(), f"<!-- block:{block_name}:start -->"))
        replacements.append((close_end, f"<!-- block:{block_name}:end -->"))
    return _apply_marker_inserts(template_string, replacements)


def _should_inject_block_marker(
    template_string: str, start_pos: int, block_name: str
) -> bool:
    if block_name in _SKIP_MARKER_BLOCKS:
        return False
    return not is_inside_no_comment_tag(template_string[:start_pos])


def _apply_marker_inserts(template_string: str, replacements) -> str:
    replacements.sort(key=lambda x: x[0], reverse=True)
    for start, marker in replacements:
        template_string = template_string[:start] + marker + template_string[start:]
    return template_string


# ── Include handling ───────────────────────────────────────────────


def _handle_includes(text: str, root_dir, depth: int = 0) -> str:
    if depth > 5:
        return text

    def replace_include(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        with_clause = None
        if " with " in expr:
            parts = expr.split(" with ", 1)
            inc_path = parts[0].strip().strip("'\"")
            with_clause = parts[1].strip()
        else:
            inc_path = expr.strip("'\"")
        expanded = _resolve_include(inc_path, root_dir, depth)
        if with_clause and not expanded.startswith("<!-- Include Error:"):
            return f"{{% with {with_clause} %}}{expanded}{{% endwith %}}"
        return expanded

    return _RE_INCLUDE.sub(replace_include, text)


def _resolve_include(inc_path: str, root_dir, depth: int) -> str:
    search_path = search_template_path(root_dir, inc_path)
    if not search_path:
        return f"<!-- Include Error: {inc_path} not found -->"
    content = read_template_file(search_path)
    if content is None:
        return "<!-- Include Error: file too large or unreadable -->"
    # Mask before recursing so an `{% include %}` example sitting inside the
    # included file's own HTML comment can't re-trigger expansion.
    content, stash = _mask_html_comments(content)
    expanded = _handle_includes(content, root_dir, depth + 1)
    return _restore_html_comments(expanded, stash)


# ── Component, filter, autoescape blocks ───────────────────────────


def _handle_components(text: str) -> str:
    while _RE_COMPONENT.search(text):
        text = _RE_COMPONENT.sub(_replace_component, text)
    return text


def _replace_component(match: re.Match[str]) -> str:
    name = match.group(1).strip()
    args = match.group(2).strip().strip(",").strip()
    content = match.group(3)
    safe_content = escape_template_literal(content)
    comma = ", " if args else ""
    return f'{{{{ component("{name}"{comma}{args}, slot="{safe_content}") }}}}'


def _replace_filter_block(m: re.Match[str]) -> str:
    filter_chain = m.group(1)
    safe_content = escape_template_literal(m.group(2))
    return f'{{{{ "{safe_content}"|{filter_chain} }}}}'


def _replace_autoescape_block(m: re.Match[str]) -> str:
    enabled = m.group(1) == "true"
    content = m.group(2)
    if enabled:
        return content
    return re.sub(r"\{\{[^}]+\}\}", _add_safe_filter, content)


def _add_safe_filter(var_match: re.Match[str]) -> str:
    expr = var_match.group(0)[2:-2].strip()
    if "|safe" in expr or expr.endswith("|safe"):
        return var_match.group(0)
    return f"{{{{ {expr}|safe }}}}"


def _neutralize_raw(m: re.Match[str]) -> str:
    return m.group(1).replace("{{", "&#123;&#123;").replace("{%", "&#123;&#37;")


# ── Macro import handling ──────────────────────────────────────────


def _process_from_imports(
    template_string: str, context: dict[str, Any], root_dir
) -> None:
    for m in _RE_FROM_IMPORT.finditer(template_string):
        _apply_from_import(m, context, root_dir)


def _apply_from_import(m, context: dict[str, Any], root_dir) -> None:
    try:
        full_path = _safe_resolve(root_dir or os.getcwd(), m.group(1))
    except ValueError:
        return
    if not os.path.exists(full_path):
        return
    names = [n.strip() for n in m.group(2).split(",")]
    context.update(_extract_macros(full_path, names, parent_ctx=context))


def _process_namespace_imports(
    template_string: str, context: dict[str, Any], root_dir
) -> None:
    for m in _RE_IMPORT_AS.finditer(template_string):
        macro_file = m.group(1)
        namespace_name = m.group(2)
        try:
            full_path = _safe_resolve(root_dir or os.getcwd(), macro_file)
        except ValueError:
            continue
        if not os.path.exists(full_path):
            continue
        all_macro_names = _get_all_macro_names(full_path)
        imported = _extract_macros(full_path, all_macro_names, parent_ctx=context)
        context[namespace_name] = type("Namespace", (), imported)()


def _register_inline_macros(template_string: str, context: dict[str, Any]) -> str:
    for match in _RE_MACRO.finditer(template_string):
        macro_name, body, raw_params = (
            match.group(1),
            match.group(3),
            match.group(2).strip(),
        )
        param_names, param_defaults, varargs, varkw = parse_macro_params(raw_params)
        context[macro_name] = make_macro(
            body,
            param_names,
            param_defaults,
            varargs,
            varkw,
            sibling_lookup=None,
            parent_ctx=context,
        )
    return _RE_MACRO.sub("", template_string)


# ── Main entry point ───────────────────────────────────────────────


def _preprocess(
    template_string: str,
    context: Optional[dict[str, Any]] = None,
    root_dir: Optional[str] = None,
    strip_blocks: bool = True,
    inject_markers: bool = False,
) -> str:
    """Resolve inheritance, includes, macros, and strip comments.

    Args:
        inject_markers: replace block tags with HTML comment markers so the
            client can target ``data-block`` regions without needing IDs.

    Returns the fully pre-processed template (still contains ``{% block %}``
    tags so callers can extract individual blocks if needed).
    """
    template_string, _html_comments = _mask_html_comments(template_string)
    template_string = _handle_inheritance(template_string, root_dir)
    template_string = _apply_block_strategy(
        template_string, strip_blocks, inject_markers
    )
    template_string = _handle_includes(template_string, root_dir)
    template_string = _handle_components(template_string)
    if context is not None:
        _process_from_imports(template_string, context, root_dir)
    template_string = _RE_FROM_IMPORT.sub("", template_string)
    if context is not None:
        _process_namespace_imports(template_string, context, root_dir)
    template_string = _RE_IMPORT_AS.sub("", template_string)
    template_string = _RE_FILTER_BLOCK.sub(_replace_filter_block, template_string)
    template_string = _RE_AUTOESCAPE_BLOCK.sub(
        _replace_autoescape_block, template_string
    )
    template_string = _RE_COMMENT.sub("", template_string)
    template_string = _RE_RAW.sub(_neutralize_raw, template_string)
    if context is not None:
        template_string = _register_inline_macros(template_string, context)
    template_string = _restore_html_comments(template_string, _html_comments)
    return template_string


def _apply_block_strategy(
    template_string: str, strip_blocks: bool, inject_markers: bool
) -> str:
    if inject_markers:
        return _inject_block_markers(template_string)
    if strip_blocks:
        template_string = _RE_BLOCK_OPEN.sub("", template_string)
        template_string = _RE_BLOCK_CLOSE.sub("", template_string)
    return template_string
