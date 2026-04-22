from __future__ import annotations

import ast
import datetime
import hashlib
import html as _html
import json
import os
import re
import secrets
from typing import Any, Iterable, Iterator, Optional, Union

from .utils import humanize

# Caches
_compiled_cache = {}  # hash(template_string) -> callable
_dotted_cache = {}  # expr -> resolved expr
_macro_cache = {}  # file_path -> file content

# Pre-compiled regex patterns
_RE_EXTENDS = re.compile(r"{%-?\s*extends\s+[\'\"](.*?)[\'\"]\s*-?%}")
_RE_INCLUDE = re.compile(r"{%-?\s*include\s+(.*?)\s*-?%}")
_RE_COMMENT = re.compile(r"\{#.*?#\}", re.DOTALL)
_RE_TOKENS = re.compile(r"(?s)({{.*?}}|{%.*?%})")
_RE_DOTTED = re.compile(
    r"""(\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*')|(\b[A-Za-z_]\w*(?:(?:\.\w+)|(?:\[[^\]]+\]))*)"""
)
# Match "name|filter|filter2(args)" sequences (for use inside {% if/for %})
_RE_FILTER_CHAIN = re.compile(
    r"(\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)((?:\s*\|\s*\w+(?:\([^)]*\))?)+)"
)
_RE_BLOCK_OPEN = re.compile(r"{%-?\s*block\s+(\w+)\s*-?%}")
_RE_BLOCK_CLOSE = re.compile(r"{%-?\s*endblock(?:\s+\w+)?\s*-?%}")
_RE_FROM_IMPORT = re.compile(r"{%-?\s*from\s+['\"](.+?)['\"]\s+import\s+(.+?)\s*-?%}")
_RE_MACRO = re.compile(
    r"{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%}(.*?){%-?\s*endmacro\s*-?%}", re.DOTALL
)

_RE_STRIPTAGS = re.compile(r"<[^>]+>")
_RE_RAW = re.compile(r"{%-?\s*raw\s*-?%}(.*?){%-?\s*endraw\s*-?%}", re.DOTALL)
_RE_COMPONENT = re.compile(
    r"{%-?\s*component\s+[\'\"](.*?)[\'\"]\s*(.*?)-?%}(.*?){%-?\s*endcomponent\s*-?%}",
    re.DOTALL,
)


def _unparse(node: Optional[ast.AST]) -> str:
    """Unparse an AST node back to a string, with fallback for Python < 3.9."""
    if node is None:
        return "None"
    if hasattr(ast, "unparse"):
        return ast.unparse(node)

    # Lightweight fallback for older Python versions
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, (ast.Constant, getattr(ast, "NameConstant", type(None)))):
        return repr(getattr(node, "value", node))
    if hasattr(ast, "Str") and isinstance(node, ast.Str):
        return repr(node.s)
    if hasattr(ast, "Num") and isinstance(node, ast.Num):
        return repr(node.n)
    if isinstance(node, ast.Attribute):
        return f"{_unparse(node.value)}.{node.attr}"
    if isinstance(node, ast.UnaryOp):
        op_map = {ast.USub: "-", ast.UAdd: "+", ast.Not: "not "}
        op = op_map.get(type(node.op), "")
        return f"{op}{_unparse(node.operand)}"
    if isinstance(node, ast.BinOp):
        return (
            f"({_unparse(node.left)} {type(node.op).__name__} {_unparse(node.right)})"
        )
    if isinstance(node, ast.Subscript):
        return f"{_unparse(node.value)}[{_unparse(node.slice)}]"
    if hasattr(ast, "Index") and isinstance(node, ast.Index):
        return _unparse(node.value)

    return ""


class SafeString(str):
    """Marks a string as safe HTML to prevent automatic escaping during rendering."""

    pass


def html_safe_json(v, **kwargs) -> SafeString:
    """Serialize object to JSON and escape <, >, & for safe inclusion in <script> tags."""
    json_str = json.dumps(v, **kwargs)
    return SafeString(
        json_str.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    )


def _escape(value: Any) -> Union[str, SafeString]:
    """Escape a value for safe HTML output. SafeString instances are returned unchanged."""
    if value is None:
        return ""
    if isinstance(value, SafeString):
        return value
    s = str(value)
    if isinstance(s, SafeString):
        return s
    return _html.escape(s, quote=True)


# --- TEMPLATE FILTERS ---
TEMPLATE_FILTERS = {
    "upper": lambda v: str(v).upper(),
    "lower": lambda v: str(v).lower(),
    "capitalize": lambda v: str(v).capitalize(),
    "title": lambda v: str(v).title(),
    "truncate": lambda v, length=100: (
        str(v)[:length] + "..." if len(str(v)) > length else str(v)
    ),
    "replace": lambda v, old, new: str(v).replace(old, new),
    "join": lambda v, sep=", ": sep.join(str(i) for i in v),
    "default": lambda v, d="": d if v is None or v == "" else v,
    "striptags": lambda v: _RE_STRIPTAGS.sub("", str(v)),
    "length": lambda v: len(v),
    "date": lambda v, f="%d/%m/%Y": _date_filter(v, f),
    "pluralize": lambda v, s, p: s if int(v) <= 1 else p,
    "safe": lambda v: SafeString(str(v)) if v is not None else SafeString(""),
    "escape": lambda v: _html.escape(str(v)) if v is not None else "",
    "e": lambda v: _html.escape(str(v)) if v is not None else "",
    "first": lambda v: v[0] if v and len(v) > 0 else None,
    "last": lambda v: v[-1] if v and len(v) > 0 else None,
    "selectattr": lambda v, attr, val=True: [
        i
        for i in v
        if (
            getattr(i, attr)
            if hasattr(i, attr)
            else (i.get(attr) if isinstance(i, dict) else None)
        )
        == val
    ],
    "abs": lambda v: abs(v),
    "tojson": lambda v, **kwargs: html_safe_json(v, **kwargs),
    "dump": lambda v, **kwargs: html_safe_json(v, **kwargs),
    "dictsort": lambda v: sorted(v.items()) if isinstance(v, dict) else v,
    # Humanize filters
    "time_ago": humanize.time_ago,
    "filesize": humanize.file_size,
    "intcomma": humanize.intcomma,
    "duration": humanize.duration,
}


# Whitelist of single-underscore attributes that templates may legitimately access.
# Everything else starting with "_" is blocked to prevent sandbox escape
# (e.g. _get_conn, _table, _db_path on ORM models).
_TEMPLATE_SAFE_ATTRS = frozenset(
    {
        "_label",
        "_error",
        "_fields",
        "_request",
        "_input_schema",
        "_output_schema",
    }
)


def _get(obj: Any, key: Union[str, int]) -> Any:
    """Access an attribute or dictionary/list key, favoring attributes for strings.

    Returns an empty string if the key/attribute is not found or the object is not subscriptsable.
    """
    if isinstance(key, str):
        # SECURITY: Block access to dunder attributes entirely
        if key.startswith("__"):
            return ""
        # SECURITY: Block single-underscore attributes unless whitelisted.
        # This prevents template sandbox escape to ORM/framework internals.
        if key.startswith("_") and key not in _TEMPLATE_SAFE_ATTRS:
            return ""
        try:
            return getattr(obj, key)
        except (AttributeError, TypeError):
            pass

    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return ""


def _resolve_name(context, name, is_debug=False):
    """Safely resolve a non-dotted name from context or builtins."""
    if name in context:
        return context[name]

    # Explicitly allowed builtins
    if name in (
        "range",
        "len",
        "dict",
        "str",
        "int",
        "float",
        "list",
        "enumerate",
        "bool",
    ):
        import builtins

        return getattr(builtins, name)

    if is_debug:
        raise NameError(f"Variable '{name}' is not defined in template context.")

    return ""


class _Loop:
    """Helper for tracking loop state (index, first, last, etc.) within template for-loops."""

    def __init__(self, iterable: Iterable[Any]):
        self._iterable = (
            list(iterable) if not hasattr(iterable, "__len__") else iterable
        )
        self.length: int = len(self._iterable)
        self.index0: int = -1

    def __iter__(self) -> Iterator[Any]:
        for item in self._iterable:
            self.index0 += 1
            yield item

    @property
    def index(self) -> int:
        """The current 1-based index of the loop."""
        return self.index0 + 1

    @property
    def first(self) -> bool:
        """True if this is the first iteration of the loop."""
        return self.index0 == 0

    @property
    def last(self) -> bool:
        """True if this is the last iteration of the loop."""
        return self.index0 == self.length - 1


def _resolve_dotted(expr, locals_set: Optional[set[str]] = None, _debug: bool = False):
    """Resolve dotted attribute/item access in a template expression.

    Converts `a.b[c]` into `_get(_get(a, "b"), c)`.
    Identifies names not in `locals_set` as context variables retrieved via `_res`.
    """
    if not expr:
        return ""

    def replace_match(m):
        if m.group(1):  # It's a string literal
            return m.group(1)
        chain = m.group(2)
        # Parse the chain into components: base, .attr, [item]
        match_start = re.search(r"[\.\[]", chain)
        if not match_start:
            # Single name
            if chain in (locals_set or set()):
                return chain
            # Literals or special names
            if (
                chain
                in (
                    "True",
                    "False",
                    "None",
                    "and",
                    "or",
                    "not",
                    "in",
                    "is",
                    "if",
                    "else",
                    "elif",
                    "for",
                    "while",
                    "lambda",
                    "yield",
                    "async",
                    "await",
                )
                or chain.isdigit()
            ):
                return chain

            # Check for keyword argument (name followed by '=' but not '==')
            pos = m.end()
            while pos < len(expr) and expr[pos].isspace():
                pos += 1
            if pos < len(expr) and expr[pos] == "=":
                if pos + 1 >= len(expr) or expr[pos + 1] != "=":
                    return chain

            return f'_res(context, "{chain}", _debug)'

        base = chain[: match_start.start()]
        suffix = chain[match_start.start() :]

        # Resolve base name
        if (
            base in (locals_set or set())
            or base
            in (
                "True",
                "False",
                "None",
                "and",
                "or",
                "not",
                "in",
                "is",
                "if",
                "else",
                "elif",
                "for",
                "while",
                "lambda",
                "yield",
                "async",
                "await",
            )
            or base.isdigit()
        ):
            current = base
        else:
            current = f'_res(context, "{base}", _debug)'

        # Find all .attr or [item]
        accessors = re.findall(r"(\.([A-Za-z_]\w*))|(\[([^\]]+)\])", suffix)

        result = current
        for dot_full, dot_name, bracket_full, bracket_content in accessors:
            if dot_full:
                result = f'_get({result}, "{dot_name}")'
            else:
                # Detect if the bracket content is a slice (e.g. data[1:5])
                try:
                    tree = ast.parse(f"x[{bracket_content}]")
                    node = tree.body[0].value.slice
                    if isinstance(node, ast.Slice):
                        lower_val = _resolve_dotted(
                            _unparse(node.lower), locals_set, _debug
                        )
                        u = _resolve_dotted(_unparse(node.upper), locals_set, _debug)
                        s = _resolve_dotted(_unparse(node.step), locals_set, _debug)
                        result = f"_get({result}, slice({lower_val}, {u}, {s}))"

                    else:
                        inner_resolved = _resolve_dotted(
                            bracket_content, locals_set, _debug
                        )
                        result = f"_get({result}, {inner_resolved})"
                except Exception:
                    inner_resolved = _resolve_dotted(
                        bracket_content, locals_set, _debug
                    )
                    result = f"_get({result}, {inner_resolved})"
        return result

    result = _RE_DOTTED.sub(replace_match, expr)
    return result


def _safe_resolve(base, requested):
    """Ensure requested path resolves within base directory."""
    base = os.path.abspath(base)
    full = os.path.abspath(os.path.join(base, requested))
    if not (full.startswith(base + os.sep) or full == base):
        raise ValueError(f"Path traversal blocked: {requested}")
    return full


def _extract_macros(file_path, names, parent_ctx=None):
    """Parse a macro file and return callables for the requested macro names.

    All macros in the file are made available to each other (sibling calls),
    so a macro can reference another macro defined in the same file.
    """
    content = _macro_cache.get(file_path)
    if content is None:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        _macro_cache[file_path] = content

    all_macros = {}
    parsed = []
    for match in _RE_MACRO.finditer(content):
        macro_name = match.group(1)
        raw_params = match.group(2).strip()
        body = match.group(3)

        param_names = []
        param_defaults = {}
        varargs = None
        varkw = None

        if raw_params:
            for param in raw_params.split(","):
                param = param.strip()
                if not param:
                    continue

                if param.startswith("**"):
                    varkw = param[2:]
                elif param.startswith("*"):
                    varargs = param[1:]
                elif "=" in param:
                    pname, pdefault = param.split("=", 1)
                    pname = pname.strip()
                    param_names.append(pname)
                    param_defaults[pname] = pdefault.strip()
                else:
                    param_names.append(param)
        parsed.append((macro_name, body, param_names, param_defaults, varargs, varkw))

    def _make_macro(m_body, m_params, m_defaults, m_varargs, m_varkw):
        def macro_fn(*args, **kwargs):
            local_ctx = dict(parent_ctx or {})
            local_ctx.update(all_macros)

            # 1. Map positional args to named params
            used_kwargs = set()
            for i, pname in enumerate(m_params):
                if i < len(args):
                    local_ctx[pname] = args[i]
                elif pname in kwargs:
                    local_ctx[pname] = kwargs[pname]
                    used_kwargs.add(pname)
                elif pname in m_defaults:
                    try:
                        local_ctx[pname] = ast.literal_eval(m_defaults[pname])
                    except (ValueError, SyntaxError):
                        local_ctx[pname] = m_defaults[pname]
                else:
                    local_ctx[pname] = ""

            # 2. Collect *varargs
            if m_varargs:
                local_ctx[m_varargs] = args[len(m_params) :]

            # 3. Collect **varkw
            if m_varkw:
                remaining = {k: v for k, v in kwargs.items() if k not in m_params}
                local_ctx[m_varkw] = remaining

            return SafeString(render_template_string(m_body, local_ctx))

        return macro_fn

    for macro_name, body, param_names, param_defaults, varargs, varkw in parsed:
        all_macros[macro_name] = _make_macro(
            body, param_names, param_defaults, varargs, varkw
        )

    return {n: all_macros[n] for n in names if n in all_macros}


def _date_filter(v, f="%d/%m/%Y"):
    """Format a date/datetime or ISO string."""
    if hasattr(v, "strftime"):
        return v.strftime(f)
    if isinstance(v, str) and len(v) >= 10:
        try:
            return datetime.datetime.fromisoformat(v).strftime(f)
        except (ValueError, TypeError):
            return v
    return v


def _preprocess(template_string, context=None, root_dir=None, strip_blocks=True):
    """Resolve inheritance, includes, macros, and strip comments.

    Returns the fully pre-processed template string (still contains
    {% block %} tags so callers can extract individual blocks).
    """

    # 1. Handle Inheritance (Extends & Block)
    def handle_inheritance(text, depth=0):
        if depth > 5:
            return text

        extends_match = _RE_EXTENDS.search(text)
        if not extends_match:
            return text

        parent_path = extends_match.group(1)
        base = (
            root_dir
            if root_dir and os.path.isabs(root_dir)
            else os.path.join(os.getcwd(), root_dir or "")
        )
        try:
            full_parent_path = _safe_resolve(base, parent_path)
        except ValueError:
            return "<!-- Inheritance Error: path traversal blocked -->"

        if not os.path.exists(full_parent_path):
            return f"<!-- Inheritance Error: {parent_path} not found in {base} -->"

        with open(full_parent_path, "r", encoding="utf-8") as f:
            parent_text = f.read()

        # Nesting-aware extraction of functional tags outside blocks
        outside_text = ""
        last_pos = 0
        block_ranges = []
        for open_match in _RE_BLOCK_OPEN.finditer(text):
            start = open_match.start()
            # If this block is already inside a previously found block, skip it
            if any(r[0] <= start < r[1] for r in block_ranges):
                continue

            # Find matching endblock
            depth_inner = 1
            pos = open_match.end()
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        block_ranges.append((start, nxt_close.end()))
                        break
                    pos = nxt_close.end()

        # Build outside_text by joining gaps between top-level blocks
        last_pos = 0
        for start, end in sorted(block_ranges):
            outside_text += text[last_pos:start]
            last_pos = end
        outside_text += text[last_pos:]

        child_orphans = []
        for m in _RE_TOKENS.finditer(outside_text):
            tag = m.group(0)
            if not any(
                tag.strip().startswith(p)
                for p in [
                    "{%- extends",
                    "{% extends",
                    "{%- block",
                    "{% block",
                    "{%- endblock",
                    "{% endblock",
                ]
            ):
                child_orphans.append(tag)

        child_logic = "\n".join(child_orphans)

        child_blocks = {}
        # Nesting-aware block extraction
        for open_match in _RE_BLOCK_OPEN.finditer(text):
            name = open_match.group(1)
            if name in child_blocks:
                continue  # already found
            start = open_match.end()
            depth_inner = 1
            pos = start
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        child_blocks[name] = text[start : nxt_close.start()]
                        break
                    pos = nxt_close.end()

        # Nesting-aware block replacement in parent
        blocks_to_replace = []
        for m in _RE_BLOCK_OPEN.finditer(parent_text):
            name = m.group(1)
            start = m.end()
            depth_inner = 1
            pos = start
            while depth_inner > 0:
                nxt_open = _RE_BLOCK_OPEN.search(parent_text, pos)
                nxt_close = _RE_BLOCK_CLOSE.search(parent_text, pos)
                if nxt_close is None:
                    break
                if nxt_open and nxt_open.start() < nxt_close.start():
                    depth_inner += 1
                    pos = nxt_open.end()
                else:
                    depth_inner -= 1
                    if depth_inner == 0:
                        blocks_to_replace.append(
                            (m.start(), nxt_close.end(), name, start, nxt_close.start())
                        )
                        break
                    pos = nxt_close.end()

        # Sort blocks by start position descending to handle nested blocks correctly
        # and keep string offsets valid during replacement.
        for full_start, full_end, name, content_start, content_end in sorted(
            blocks_to_replace, key=lambda x: x[0], reverse=True
        ):
            content = child_blocks.get(name, parent_text[content_start:content_end])
            replacement = f"{{% block {name} %}}{content}{{% endblock %}}"
            parent_text = (
                parent_text[:full_start] + replacement + parent_text[full_end:]
            )
        if child_logic:
            parent_text = child_logic + "\n" + parent_text
        return handle_inheritance(parent_text, depth + 1)

    template_string = handle_inheritance(template_string)

    # 1.5. Optional block tag stripping
    if strip_blocks:
        template_string = _RE_BLOCK_OPEN.sub("", template_string)
        template_string = _RE_BLOCK_CLOSE.sub("", template_string)

    # 2. Pre-process includes recursively
    def handle_includes(text, depth=0):
        if depth > 5:
            return text

        def replace_include(match):
            inc_path = match.group(1).strip("'\"")
            try:
                search_path = _safe_resolve(root_dir or os.getcwd(), inc_path)
            except ValueError:
                return "<!-- Include Error: path traversal blocked -->"
            if os.path.exists(search_path):
                try:
                    with open(search_path, "r", encoding="utf-8") as f:
                        return handle_includes(f.read(), depth + 1)
                except Exception:
                    return f"<!-- Error reading {inc_path} -->"
            return f"<!-- Include Error: {inc_path} not found -->"

        return _RE_INCLUDE.sub(replace_include, text)

    template_string = handle_includes(template_string)

    # 3. Pre-process component blocks (Slots)
    def handle_components(text):
        def replace_comp(match):
            name = match.group(1).strip()
            args = match.group(2).strip()
            # Clean leading/trailing comma if any
            args = args.strip(",").strip()
            content = match.group(3)
            # Escape content for inclusion in a string literal
            safe_content = (
                content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            )
            comma = ", " if args else ""
            return f'{{{{ component("{name}"{comma}{args}, slot="{safe_content}") }}}}'

        while _RE_COMPONENT.search(text):
            text = _RE_COMPONENT.sub(replace_comp, text)
        return text

    template_string = handle_components(template_string)

    # 3. Handle macro imports: {% from "file" import name1, name2 %}
    for m in _RE_FROM_IMPORT.finditer(template_string):
        macro_file = m.group(1)
        names = [n.strip() for n in m.group(2).split(",")]
        try:
            full_path = _safe_resolve(root_dir or os.getcwd(), macro_file)
        except ValueError:
            continue
        if os.path.exists(full_path):
            imported = _extract_macros(full_path, names, parent_ctx=context)
            context.update(imported)
    template_string = _RE_FROM_IMPORT.sub("", template_string)

    # 4. Strip comments {# ... #}
    template_string = _RE_COMMENT.sub("", template_string)

    # 5. Protect {% raw %}...{% endraw %} content by escaping template syntax
    def _neutralize_raw(m):
        content = m.group(1)
        return content.replace("{{", "&#123;&#123;").replace("{%", "&#123;&#37;")

    template_string = _RE_RAW.sub(_neutralize_raw, template_string)

    return template_string


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


def _apply_filters(
    val_expr, filters_str, locals_set: Optional[set[str]] = None, _debug: bool = False
):
    """Given 'name' and '|upper|truncate(10)', build filter call chain."""
    for filter_part in filters_str.split("|")[1:]:
        filter_part = filter_part.strip()
        if not filter_part:
            continue
        if "(" in filter_part:
            fname, fargs = filter_part.split("(", 1)
            fargs = fargs.rstrip(")")
            # Note: naive argument split, doesn't handle nested parens perfectly
            # but better than nothing for security resolution
            # Smart argument split that respects quotes
            resolved_args = []
            if fargs:
                # Regex to match: quoted strings OR non-comma sequences
                arg_matches = re.finditer(
                    r"(\"(?:\\.|[^\"\\])*\"|\'(?:\\.|[^\'\\])*\'|[^,]+)", fargs
                )
                for am in arg_matches:
                    arg_val = am.group(0).strip()
                    if arg_val:
                        resolved_args.append(_resolve_expr(arg_val, locals_set, _debug))
            args_str = ", ".join(resolved_args)
            val_expr = f"__filters['{fname.strip()}']({val_expr}, {args_str})"
        else:
            val_expr = f"__filters['{filter_part}']({val_expr})"
    return val_expr


def _resolve_expr(expr, locals_set: Optional[set[str]] = None, _debug: bool = False):
    """Resolve dotted access + filter piping. Works in {{ }} and {% if/for %}.

    Handles chains like a.b|upper|truncate(10) anywhere in the expression.
    """

    def replace_filter(m):
        base = _resolve_dotted(m.group(1), locals_set, _debug)
        return _apply_filters(base, m.group(2), locals_set, _debug)

    # First replace filter chains (name|filter|filter2)
    expr = _RE_FILTER_CHAIN.sub(replace_filter, expr)
    # Then resolve remaining dotted accesses
    return _resolve_dotted(expr, locals_set, _debug)


def _split_expr_and_filters(expr):
    """Split 'some_expr | filter1 | filter2(arg)' into (expr_part, '|filter1|filter2(arg)').

    Correctly handles parentheses in the expression part so that
    '(u.email or "A")[:1] | upper' → ('(u.email or "A")[:1]', '|upper').
    Returns (full_expr, '') when no pipe filter is found outside parens/brackets.
    """
    depth = 0  # paren/bracket nesting depth
    in_str = None  # current string delimiter
    for i, ch in enumerate(expr):
        if in_str:
            if ch == in_str and (i == 0 or expr[i - 1] != "\\"):
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch in ("(", "[", "{"):
            depth += 1
        elif ch in (")", "]", "}"):
            depth -= 1
        elif ch == "|" and depth == 0:
            return expr[:i].rstrip(), expr[i:]
    return expr, ""


def _resolve_expr_full(
    expr, locals_set: Optional[set[str]] = None, _debug: bool = False
):
    """Like _resolve_expr but also handles complex base expressions (with parens)
    before a pipe chain, e.g.  {{ (u.email or 'A')[:1] | upper }}.
    """
    base_raw, filters_raw = _split_expr_and_filters(expr)
    if filters_raw:
        # Resolve dotted attrs inside the base expression
        base_resolved = _resolve_dotted(base_raw.strip(), locals_set, _debug)
        return _apply_filters(base_resolved, filters_raw, locals_set, _debug)
    # No pipe filter: fall back to normal resolution
    return _resolve_expr(expr, locals_set, _debug)


def _compile_and_run(template_string, context, is_debug: bool = False):
    """Compile a pre-processed template string and execute it."""
    cache_key = hashlib.md5(template_string.encode()).hexdigest()
    if is_debug:
        cache_key += "_debug"
    run_fn = _compiled_cache.get(cache_key)

    if run_fn is None:
        tokens = _RE_TOKENS.split(template_string)
        code = [
            "def __run_template(context, __filters, _get, _res, _debug):",
            "    pass",
        ]
        indent = 4
        # track defined local variables per level (stack)
        local_scope_stack: list[set[str]] = [
            {"context", "__filters", "_get", "_res", "_debug"}
        ]
        block_stack = []

        def get_all_locals():
            s = set()
            for stack_level in local_scope_stack:
                s.update(stack_level)
            return s

        for token in tokens:
            if token.startswith("{{"):
                expr = token[2:-2].strip().lstrip("-").rstrip("-").strip()
                resolved = _resolve_expr_full(expr, get_all_locals(), is_debug)
                code.append(" " * indent + f"yield _escape({resolved})")
            elif token.startswith("{%"):
                stmt = token[2:-2].strip().lstrip("-").rstrip("-").strip()
                if stmt.startswith("set "):
                    var_name, expr = stmt[4:].split("=", 1)
                    var_name = var_name.strip()
                    local_scope_stack[-1].add(var_name)
                    code.append(
                        " " * indent
                        + f"{var_name} = {_resolve_expr_full(expr.strip(), get_all_locals(), is_debug)}"
                    )
                elif stmt.startswith("with "):
                    # {% with x = expr %}
                    var_part = stmt[5:]
                    if "=" in var_part:
                        var_name, expr = var_part.split("=", 1)
                        var_name = var_name.strip()
                        local_scope_stack[-1].add(var_name)
                        code.append(
                            " " * indent
                            + f"{var_name} = {_resolve_expr_full(expr.strip(), get_all_locals(), is_debug)}"
                        )
                elif (
                    stmt.startswith("for ")
                    or stmt.startswith("if ")
                    or stmt.startswith("elif ")
                    or stmt.startswith("else")
                ):
                    if stmt.startswith("elif ") or stmt.startswith("else"):
                        indent -= 4
                        local_scope_stack.pop()
                        local_scope_stack.append(set())

                    if stmt.startswith("for "):
                        try:
                            loop_vars_part, collection = stmt[4:].split(" in ", 1)
                            loop_id = secrets.token_hex(4)
                            block_stack.append(("for", loop_id))

                            loop_vars = [v.strip() for v in loop_vars_part.split(",")]
                            # Collection resolution (must not see loop vars of THIS loop)
                            coll_resolved = _resolve_expr(
                                collection.strip(), get_all_locals(), is_debug
                            )

                            code.append(
                                f"{' ' * indent}__loop_{loop_id} = _Loop({coll_resolved})"
                            )
                            code.append(
                                f"{' ' * indent}for {loop_vars_part.strip()} in __loop_{loop_id}:"
                            )
                            indent += 4
                            code.append(f"{' ' * indent}loop = __loop_{loop_id}")
                            # New scope for loop body (including 'loop' helper)
                            new_scope = set(["loop"])
                            for v in loop_vars:
                                if v:
                                    new_scope.add(v)
                            local_scope_stack.append(new_scope)
                        except ValueError:
                            block_stack.append(("for", None))
                            code.append(" " * indent + "for _ in []:")  # fallback
                            indent += 4
                            local_scope_stack.append(set())
                    elif stmt.startswith("if "):
                        block_stack.append(("if", None))
                        code.append(
                            " " * indent
                            + "if "
                            + _resolve_expr(
                                stmt[3:].strip(), get_all_locals(), is_debug
                            )
                            + ":"
                        )
                        indent += 4
                        local_scope_stack.append(set())
                    elif stmt.startswith("else"):
                        if block_stack and block_stack[-1][0] == "for":
                            loop_id = block_stack[-1][1]
                            if loop_id:
                                code.append(
                                    f"{' ' * indent}if not __loop_{loop_id}.length:"
                                )
                            else:
                                code.append(f"{' ' * indent}else:")
                        else:
                            code.append(" " * indent + "else:")
                        indent += 4
                    elif stmt.startswith("elif "):
                        code.append(
                            " " * indent
                            + "elif "
                            + _resolve_expr(
                                stmt[5:].strip(), get_all_locals(), is_debug
                            )
                            + ":"
                        )
                        indent += 4
                elif stmt in ["endif", "endfor"]:
                    if block_stack:
                        block_stack.pop()
                    indent -= 4
                    local_scope_stack.pop()
            else:
                if token:
                    safe_token = repr(token)
                    code.append(" " * indent + f"yield {safe_token}")

        compiled_code = "\n".join(code)
        # SECURITY: Restrict the exec namespace. Setting __builtins__ to an
        # empty dict prevents compiled template code from accessing dangerous
        # Python builtins (import, eval, exec, open, __import__, etc.).
        # Safe builtins (range, len, str, etc.) are provided explicitly via
        # the _resolve_name() function during template execution.
        # Note: 'slice' is added to env because the AST parser translates
        # [x:y] into explicit slice() calls in the generated code.
        env = {"__builtins__": {}, "_Loop": _Loop, "_escape": _escape, "slice": slice}
        try:
            exec(compiled_code, env)
        except Exception as e:
            raise Exception(
                f"Template Compilation Error: {str(e)}\n\nCode:\n{compiled_code}"
            )
        run_fn = env["__run_template"]
        _compiled_cache[cache_key] = run_fn

    return run_fn(context, TEMPLATE_FILTERS, _get, _resolve_name, is_debug)


def render_template_string(
    template_string: str, context: dict[str, Any], root_dir: Optional[str] = None
) -> str:
    """Compile and render a template string with the provided context."""
    is_debug = False
    req = context.get("request")
    if req:
        app = getattr(req, "environ", {}).get("asok.app")
        if app:
            is_debug = app.config.get("DEBUG", False)

    template_string = _preprocess(template_string, context, root_dir)
    res = _compile_and_run(template_string, context, is_debug)
    return "".join(res) if res is not None else ""


def stream_template_string(
    template_string: str, context: dict[str, Any], root_dir: Optional[str] = None
) -> Iterator[str]:
    """Compile and stream a template string, yielding results as they are generated."""
    template_string = _preprocess(template_string, context, root_dir)
    return _compile_and_run(template_string, context)
