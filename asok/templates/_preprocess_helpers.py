"""Helpers extracted from ``preprocessor._preprocess``.

The original function did inheritance, includes, block-marker injection,
macro parsing and registration — all in one place at CC 32. Splitting each
concern out keeps every helper at A complexity.
"""

from __future__ import annotations

import ast
import os
from typing import Any, Optional

from .safestring import SafeString

# ── Macro parameter parsing ─────────────────────────────────────────


def parse_macro_params(
    raw_params: str,
) -> tuple[list[str], dict[str, str], Optional[str], Optional[str]]:
    """Parse ``name, x=1, *args, **kwargs`` style macro params."""
    param_names: list[str] = []
    param_defaults: dict[str, str] = {}
    varargs: Optional[str] = None
    varkw: Optional[str] = None
    for param in (p.strip() for p in raw_params.split(",")):
        varargs, varkw = _absorb_param(
            param, param_names, param_defaults, varargs, varkw
        )
    return param_names, param_defaults, varargs, varkw


def _absorb_param(
    param: str,
    param_names: list[str],
    param_defaults: dict[str, str],
    varargs: Optional[str],
    varkw: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    if not param:
        return varargs, varkw
    if param.startswith("**"):
        return varargs, param[2:]
    if param.startswith("*"):
        return param[1:], varkw
    if "=" in param:
        pname, pdefault = param.split("=", 1)
        pname = pname.strip()
        param_names.append(pname)
        param_defaults[pname] = pdefault.strip()
        return varargs, varkw
    param_names.append(param)
    return varargs, varkw


def bind_macro_args(
    args: tuple,
    kwargs: dict,
    m_params: list[str],
    m_defaults: dict[str, str],
    m_varargs: Optional[str],
    m_varkw: Optional[str],
    local_ctx: dict,
) -> set[str]:
    used_kwargs: set[str] = set()
    for i, pname in enumerate(m_params):
        _bind_param(pname, i, args, kwargs, m_defaults, local_ctx, used_kwargs)
    _bind_var_collectors(args, kwargs, m_params, m_varargs, m_varkw, local_ctx)
    if "caller" in kwargs and "caller" not in used_kwargs:
        local_ctx["caller"] = kwargs["caller"]
    return used_kwargs


def _bind_var_collectors(
    args: tuple,
    kwargs: dict,
    m_params: list[str],
    m_varargs: Optional[str],
    m_varkw: Optional[str],
    local_ctx: dict,
) -> None:
    if m_varargs:
        local_ctx[m_varargs] = args[len(m_params) :]
    if m_varkw:
        local_ctx[m_varkw] = {k: v for k, v in kwargs.items() if k not in m_params}


def _bind_param(
    pname: str,
    i: int,
    args: tuple,
    kwargs: dict,
    m_defaults: dict[str, str],
    local_ctx: dict,
    used_kwargs: set[str],
) -> None:
    if i < len(args):
        local_ctx[pname] = args[i]
    elif pname in kwargs:
        local_ctx[pname] = kwargs[pname]
        used_kwargs.add(pname)
    elif pname in m_defaults:
        local_ctx[pname] = _safe_literal_eval(m_defaults[pname])
    else:
        local_ctx[pname] = ""


def _safe_literal_eval(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


# ── Template path resolution ────────────────────────────────────────


def _check_traversal(b_abs: str, full: str, requested: str) -> None:
    if not (full.startswith(b_abs + os.sep) or full == b_abs):
        raise ValueError(f"Path traversal blocked: {requested}")


def safe_resolve(base, requested: str) -> str:
    """Resolve ``requested`` against ``base`` (a single dir or an iterable).

    When ``base`` is iterable, each candidate dir is tried in order and the
    first one whose resolved path exists on disk wins. If none exist, the
    path computed from the first base is returned — callers' existence checks
    will then fail cleanly. Each candidate is still subject to a path-traversal
    guard.
    """
    bases = list(base) if isinstance(base, (list, tuple)) else [base]
    first_full: Optional[str] = None
    for b in bases:
        b_abs = os.path.abspath(b)
        full = os.path.abspath(os.path.join(b_abs, requested))
        _check_traversal(b_abs, full, requested)
        if first_full is None:
            first_full = full
        if os.path.isfile(full):
            return full
    return first_full  # type: ignore[return-value]


def search_template_path(root_dir, requested: str) -> Optional[str]:
    """Look up ``requested`` under each candidate root; try ``.html``/``.asok`` extensions."""
    for base in _search_dirs(root_dir):
        try:
            cand_path = safe_resolve(base, requested)
        except ValueError:
            continue
        found = _resolve_candidate(cand_path)
        if found:
            return found
    return None


def _search_dirs(root_dir):
    if isinstance(root_dir, (list, tuple)):
        return [_abspath_if_needed(d) for d in root_dir]
    return [_abspath_if_needed(root_dir or os.getcwd())]


def _abspath_if_needed(path: str) -> str:
    return path if os.path.isabs(path) else os.path.abspath(path)


def _resolve_candidate(cand_path: str) -> Optional[str]:
    for ext in ("", ".html", ".asok"):
        test_path = cand_path + ext if ext else cand_path
        if os.path.isfile(test_path):
            return test_path
    return _resolve_swapped_extension(cand_path)


def _resolve_swapped_extension(cand_path: str) -> Optional[str]:
    base_path, current_ext = os.path.splitext(cand_path)
    if current_ext == ".html" and os.path.exists(base_path + ".asok"):
        return base_path + ".asok"
    if current_ext == ".asok" and os.path.exists(base_path + ".html"):
        return base_path + ".html"
    return None


def read_template_file(path: str, max_size: int = 1_000_000) -> Optional[str]:
    """Read with a size cap; returns None on oversize/IO failure."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size > max_size:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


# ── Block range finding (nesting-aware) ─────────────────────────────


def find_block_close(
    text: str, start_pos: int, re_open, re_close
) -> Optional[tuple[int, int]]:
    """Return ``(content_end, end_of_close_tag)`` for the matching endblock."""
    depth = 1
    pos = start_pos
    while depth > 0:
        nxt_open = re_open.search(text, pos)
        nxt_close = re_close.search(text, pos)
        if nxt_close is None:
            return None
        depth, pos, result = _advance_block_depth(depth, nxt_open, nxt_close)
        if result is not None:
            return result
    return None


def _advance_block_depth(depth: int, nxt_open, nxt_close):
    if nxt_open and nxt_open.start() < nxt_close.start():
        return depth + 1, nxt_open.end(), None
    depth -= 1
    if depth == 0:
        return depth, nxt_close.end(), (nxt_close.start(), nxt_close.end())
    return depth, nxt_close.end(), None


def top_level_block_ranges(text: str, re_open, re_close) -> list[tuple[int, int]]:
    """Ranges ``(open_start, close_end)`` of top-level ``{% block %}`` regions."""
    ranges: list[tuple[int, int]] = []
    for open_match in re_open.finditer(text):
        start = open_match.start()
        if any(r[0] <= start < r[1] for r in ranges):
            continue
        close = find_block_close(text, open_match.end(), re_open, re_close)
        if close:
            ranges.append((start, close[1]))
    return ranges


def extract_child_blocks(text: str, re_open, re_close) -> dict[str, str]:
    """Map ``block_name`` → inner content for each ``{% block name %}…{% endblock %}``."""
    child_blocks: dict[str, str] = {}
    for open_match in re_open.finditer(text):
        name = open_match.group(1)
        if name in child_blocks:
            continue
        start = open_match.end()
        close = find_block_close(text, start, re_open, re_close)
        if close:
            content_end, _ = close
            child_blocks[name] = text[start:content_end]
    return child_blocks


# ── Macro factory (used by both file + inline macro paths) ──────────


def make_macro(
    body: str,
    param_names: list[str],
    param_defaults: dict[str, str],
    varargs: Optional[str],
    varkw: Optional[str],
    sibling_lookup: Optional[dict[str, Any]] = None,
    parent_ctx: Optional[dict[str, Any]] = None,
):
    """Build the macro callable.

    ``sibling_lookup`` is updated in place so sibling macros defined in the
    same file resolve at call time (post-parse) rather than at definition time.
    """

    def macro_fn(*args: Any, **kwargs: Any) -> SafeString:
        local_ctx = dict(parent_ctx or {})
        if sibling_lookup:
            local_ctx.update(sibling_lookup)
        bind_macro_args(
            args, kwargs, param_names, param_defaults, varargs, varkw, local_ctx
        )
        from .engine import render_template_string

        return SafeString(render_template_string(body, local_ctx))

    return macro_fn


# ── No-comment-zone detection ───────────────────────────────────────


def is_inside_no_comment_tag(text_before: str) -> bool:
    """True when ``text_before`` ends inside <style>/<script>/<title>/<meta description>."""
    for tag in ("style", "script", "title"):
        if _inside_open_tag(text_before, tag):
            return True
    return _inside_description_meta(text_before)


def _inside_open_tag(text_before: str, tag: str) -> bool:
    last_open = text_before.rfind(f"<{tag}")
    if last_open == -1:
        return False
    return text_before.rfind(f"</{tag}>", last_open) == -1


def _inside_description_meta(text_before: str) -> bool:
    if "<meta" not in text_before or 'name="description"' not in text_before:
        return False
    last_meta = text_before.rfind("<meta")
    if last_meta == -1:
        return False
    return 'name="description"' in text_before[last_meta:]


# ── Outside-block tag extraction (child template orphan tags) ──────


_ORPHAN_PREFIXES = (
    "{%- extends",
    "{% extends",
    "{%- block",
    "{% block",
    "{%- endblock",
    "{% endblock",
)


def extract_child_orphans(outside_text: str, re_tokens) -> list[str]:
    return [
        m.group(0)
        for m in re_tokens.finditer(outside_text)
        if not _is_orphan_block_tag(m.group(0))
    ]


def _is_orphan_block_tag(tag: str) -> bool:
    stripped = tag.strip()
    return any(stripped.startswith(p) for p in _ORPHAN_PREFIXES)


# ── Safe content quoting (for component/filter blocks) ─────────────


def escape_template_literal(content: str) -> str:
    return content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
