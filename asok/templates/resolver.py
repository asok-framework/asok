from __future__ import annotations

import ast
import re
from typing import Optional

_RE_DOTTED = re.compile(
    r"""(\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*')|(\b[A-Za-z_]\w*(?:(?:\.\w+)|(?:\[[^\]]+\]))*)"""
)
# Match "name|filter|filter2(args)" sequences (for use inside {% if/for %})
_RE_FILTER_CHAIN = re.compile(
    r"(\b[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)((?:\s*\|\s*\w+(?:\([^)]*\))?)+)"
)


_UNARY_OPS = {ast.USub: "-", ast.UAdd: "+", ast.Not: "not "}


def _unparse(node: Optional[ast.AST]) -> str:
    """Unparse an AST node back to a string, with fallback for Python < 3.9."""
    if node is None:
        return "None"
    if hasattr(ast, "unparse"):
        return ast.unparse(node)
    return _unparse_fallback(node)


def _unparse_fallback(node: ast.AST) -> str:
    handler = _UNPARSE_HANDLERS.get(type(node))
    if handler is not None:
        return handler(node)
    return _unparse_legacy(node)


def _unparse_legacy(node: ast.AST) -> str:
    # Pre-3.9 nodes (Index/Str/Num); kept so the fallback handles them too.
    Index = getattr(ast, "Index", None)
    if Index is not None and isinstance(node, Index):
        return _unparse(node.value)
    return _unparse_str_or_num(node)


def _unparse_str_or_num(node: ast.AST) -> str:
    Str = getattr(ast, "Str", None)
    if Str is not None and isinstance(node, Str):
        return repr(node.s)
    Num = getattr(ast, "Num", None)
    if Num is not None and isinstance(node, Num):
        return repr(node.n)
    return ""


def _unparse_constant(node) -> str:
    return repr(getattr(node, "value", node))


def _unparse_unary(node) -> str:
    op = _UNARY_OPS.get(type(node.op), "")
    return f"{op}{_unparse(node.operand)}"


def _unparse_binop(node) -> str:
    return f"({_unparse(node.left)} {type(node.op).__name__} {_unparse(node.right)})"


_UNPARSE_HANDLERS = {
    ast.Name: lambda n: n.id,
    ast.Constant: _unparse_constant,
    ast.Attribute: lambda n: f"{_unparse(n.value)}.{n.attr}",
    ast.UnaryOp: _unparse_unary,
    ast.BinOp: _unparse_binop,
    ast.Subscript: lambda n: f"{_unparse(n.value)}[{_unparse(n.slice)}]",
}


def _resolve_dotted(
    expr: str, locals_set: Optional[set[str]] = None, _debug: bool = False
) -> str:
    """Resolve dotted attribute/item access in a template expression.

    Converts `a.b[c]` into `_get(_get(a, "b"), c)`.
    Identifies names not in `locals_set` as context variables retrieved via `_res`.

    SECURITY: Expression length limits prevent ReDoS attacks.
    """
    if not expr:
        return ""

    # SECURITY: Limit expression length to prevent ReDoS (max 5000 chars)
    if len(expr) > 5_000:
        return '""'

    def replace_match(m: re.Match[str]) -> str:
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
        for dot_full, dot_name, _bracket_full, bracket_content in accessors:
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


def _apply_filters(
    val_expr: str,
    filters_str: str,
    locals_set: Optional[set[str]] = None,
    _debug: bool = False,
) -> str:
    """Given 'name' and '|upper|truncate(10)', build filter call chain.

    SECURITY: caps filter count and argument size to prevent DoS.
    """
    for filter_part in _normalize_filter_parts(filters_str):
        val_expr = _apply_one_filter(val_expr, filter_part, locals_set, _debug)
    return val_expr


def _normalize_filter_parts(filters_str: str) -> list[str]:
    parts = filters_str.split("|")[1:]
    parts = parts[:20]
    out: list[str] = []
    for p in parts:
        stripped = p.strip()
        if stripped and len(stripped) <= 1_000:
            out.append(stripped)
    return out


def _apply_one_filter(
    val_expr: str, filter_part: str, locals_set, _debug: bool
) -> str:
    if "(" not in filter_part:
        return f"__filters['{filter_part}']({val_expr})"
    fname, fargs = filter_part.split("(", 1)
    fargs = fargs.rstrip(")")[:500]
    args_str = ", ".join(_split_filter_args(fargs, locals_set, _debug))
    return f"__filters['{fname.strip()}']({val_expr}, {args_str})"


_FILTER_ARG_RE = re.compile(r"(\"(?:\\.|[^\"\\])*\"|\'(?:\\.|[^\'\\])*\'|[^,]+)")


def _split_filter_args(fargs: str, locals_set, _debug: bool) -> list[str]:
    if not fargs:
        return []
    resolved: list[str] = []
    for am in _FILTER_ARG_RE.finditer(fargs):
        if len(resolved) >= 10:
            break
        arg_val = am.group(0).strip()
        if arg_val:
            resolved.append(_resolve_expr(arg_val, locals_set, _debug))
    return resolved


def _resolve_expr(
    expr: str, locals_set: Optional[set[str]] = None, _debug: bool = False
) -> str:
    """Resolve dotted access + filter piping + is tests. Works in {{ }} and {% if/for %}.

    Handles chains like a.b|upper|truncate(10) anywhere in the expression.
    Also handles 'is' tests like: variable is defined, number is even.

    SECURITY: Expression length limits prevent ReDoS attacks.
    """
    # SECURITY: Limit expression length to prevent ReDoS (max 5000 chars)
    if len(expr) > 5_000:
        return '""'

    # Handle "is not" and "is" tests
    # Pattern: value is [not] test_name
    def replace_is_test(m: re.Match[str]) -> str:
        value_expr = m.group(1).strip()
        negated = m.group(2) is not None  # "not" present
        test_name = m.group(3).strip().lower()  # Normalize to lowercase

        resolved_value = _resolve_dotted(value_expr, locals_set, _debug)

        # Use conditional expression to avoid lambda that would be transformed
        test_call = f"(_get(__tests, '{test_name}')({resolved_value}) if '{test_name}' in __tests else False)"

        if negated:
            return f"(not {test_call})"
        else:
            return test_call

    # SECURITY FIX: Only apply "is test" pattern outside of string literals
    # to prevent strings like '2FA is Enabled' from being incorrectly parsed
    def apply_is_test_outside_strings(text: str) -> str:
        """Apply is test pattern only to parts outside string literals."""
        result_parts = []
        i = 0
        while i < len(text):
            # Check if we're at the start of a string literal
            if text[i] in ('"', "'"):
                quote_char = text[i]
                # Find the end of the string literal
                string_start = i
                i += 1
                while i < len(text):
                    if text[i] == "\\" and i + 1 < len(text):
                        # Skip escaped character
                        i += 2
                    elif text[i] == quote_char:
                        # End of string
                        i += 1
                        break
                    else:
                        i += 1
                # Add the entire string literal unchanged
                result_parts.append(text[string_start:i])
            else:
                # Find the next string literal or end of text
                next_quote = len(text)
                for j in range(i, len(text)):
                    if text[j] in ('"', "'"):
                        next_quote = j
                        break
                # Apply is test pattern to this non-string segment
                segment = text[i:next_quote]
                is_test_pattern = re.compile(
                    r"([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*|\[[^\]]+\])*)\s+is\s+(not\s+)?(\w+)"
                )
                segment = is_test_pattern.sub(replace_is_test, segment)
                result_parts.append(segment)
                i = next_quote
        return "".join(result_parts)

    expr = apply_is_test_outside_strings(expr)

    def replace_filter(m: re.Match[str]) -> str:
        base = _resolve_dotted(m.group(1), locals_set, _debug)
        return _apply_filters(base, m.group(2), locals_set, _debug)

    # First replace filter chains (name|filter|filter2)
    expr = _RE_FILTER_CHAIN.sub(replace_filter, expr)
    # Then resolve remaining dotted accesses
    return _resolve_dotted(expr, locals_set, _debug)


def _split_expr_and_filters(expr: str) -> tuple[str, str]:
    """Split 'some_expr | filter1 | filter2(arg)' into (expr_part, '|filter1|filter2(arg)').

    Correctly handles parentheses in the expression part so that
    '(u.email or "A")[:1] | upper' → ('(u.email or "A")[:1]', '|upper').
    Returns (full_expr, '') when no pipe filter is found outside parens/brackets.
    """
    state = _SplitState()
    for i, ch in enumerate(expr):
        if state.advance(expr, i, ch):
            return expr[:i].rstrip(), expr[i:]
    return expr, ""


class _SplitState:
    __slots__ = ("depth", "in_str")

    def __init__(self) -> None:
        self.depth = 0
        self.in_str: Optional[str] = None

    def advance(self, expr: str, i: int, ch: str) -> bool:
        if self.in_str:
            self._handle_in_string(expr, i, ch)
            return False
        if ch in ('"', "'"):
            self.in_str = ch
            return False
        self._adjust_depth(ch)
        return ch == "|" and self.depth == 0

    def _adjust_depth(self, ch: str) -> None:
        if ch in ("(", "[", "{"):
            self.depth += 1
        elif ch in (")", "]", "}"):
            self.depth -= 1

    def _handle_in_string(self, expr: str, i: int, ch: str) -> None:
        if ch == self.in_str and (i == 0 or expr[i - 1] != "\\"):
            self.in_str = None


def _resolve_expr_full(
    expr: str, locals_set: Optional[set[str]] = None, _debug: bool = False
) -> str:
    """Like _resolve_expr but also handles complex base expressions (with parens)
    before a pipe chain, e.g.  {{ (u.email or 'A')[:1] | upper }}.

    SECURITY: Expression length limits prevent ReDoS attacks.
    """
    # SECURITY: Limit expression length to prevent ReDoS (max 5000 chars)
    if len(expr) > 5_000:
        return '""'

    base_raw, filters_raw = _split_expr_and_filters(expr)
    if filters_raw:
        # Resolve dotted attrs inside the base expression
        base_resolved = _resolve_dotted(base_raw.strip(), locals_set, _debug)
        return _apply_filters(base_resolved, filters_raw, locals_set, _debug)
    # No pipe filter: fall back to normal resolution
    return _resolve_expr(expr, locals_set, _debug)
