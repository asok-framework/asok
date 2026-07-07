"""SECURITY: validate user-supplied directive expressions.

Splits the original monolithic validator into focused helpers:
- ``normalize_js_expression`` turns a JS-ish expression into Python-AST-parsable form
- ``contains_dangerous_pattern`` blocks server-side and client-side injection patterns
- ``is_safe_ast`` walks the parsed AST against an allow-list

Kept private to ``asok.core``; called from ``AssetMixin._validate_expression_cached``.
"""

from __future__ import annotations

import ast
import functools
import re

from ._js_parser import (
    convert_ternary,
    extract_arrow_functions,
    parse_js_if_statement,
    split_js_statements,
)

_DANGEROUS_PATTERNS = (
    # Python server-side injection
    r"\b__import__\b",
    r"\beval\b",
    r"\bexec\b",
    r"\bcompile\b",
    r"\bopen\b\s*\(",
    r"\bfile\b\s*\(",
    r"\b__\w+__\b",
    r"\bglobals\b",
    r"\blocals\b",
    r"\bvars\b",
    r"\bgetattr\b",
    r"\bsetattr\b",
    r"\bdelattr\b",
    r"\bdir\b\s*\(",
    r"\bhelp\b\s*\(",
    # JavaScript client-side dangerous APIs
    r"\bwindow\.fetch\b",
    r'\bfetch\s*\(\s*[\'"]https?://',
    r"\bXMLHttpRequest\b",
    r"\bsendBeacon\b",
    r"\bwindow\.location\b",
    r"\bdocument\.location\b",
    r"\blocation\.replace\b",
    r"\blocation\.href\s*=",
    r"\bwindow\.open\b",
    r"\bwindow\.eval\b",
    r"\bdocument\.write\b",
    r"\bdocument\.writeln\b",
    r"\bdocument\.createElement\b",
    r"\.innerHTML\s*=",
    # Constructor-based eval bypasses
    r"\bconstructor\b",
    r"\bprototype\b",
    r"\bFunction\s*\(",
    # Template literals with interpolation
    r"`.*\$\{.*\}.*`",
    # Extra client-side restrictions
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
    r"\bdocument\.cookie\b",
    r"\bindexedDB\b",
    r"\bWebSocket\b",
    r"\bEventSource\b",
    r"\bpostMessage\b",
    r"\balert\b",
)

_ALLOWED_AST_NODES = frozenset(
    {
        ast.Expression,
        ast.Module,
        ast.Expr,
        ast.Load,
        ast.Store,
        ast.Name,
        ast.Constant,
        ast.Attribute,
        ast.Subscript,
        ast.BinOp,
        ast.UnaryOp,
        ast.Compare,
        ast.BoolOp,
        ast.IfExp,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Set,
        ast.Call,
        ast.Index,
        ast.Slice,
        ast.Assign,
        ast.AugAssign,
        ast.AnnAssign,
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.And,
        ast.Or,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
        ast.Lambda,
        ast.arguments,
        ast.arg,
        ast.Await,
    }
)

_ALLOWED_AST_OPS = frozenset(
    {
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.And,
        ast.Or,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.In,
        ast.NotIn,
        ast.Is,
        ast.IsNot,
    }
)

_FORBIDDEN_NAMES = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "__builtins__",
        "__dict__",
        "__class__",
        "__bases__",
        "__subclasses__",
        # JavaScript browser globals
        "window",
        "location",
        "globalThis",
        "XMLHttpRequest",
    }
)

_BLOCKED_JS_PROPERTIES = frozenset(
    {
        "constructor",
        "prototype",
        "getPrototypeOf",
        "getOwnPropertyDescriptor",
        "getOwnPropertyNames",
        "__proto__",
        # JavaScript browser globals
        "window",
        "location",
        "globalThis",
        "XMLHttpRequest",
    }
)

_DANGEROUS_FUNCTIONS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "file",
        "input",
        "raw_input",
        "execfile",
        "reload",
        "vars",
        "locals",
        "globals",
        "dir",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "Function",
        "alert",
    }
)


@functools.lru_cache(maxsize=2048)
def is_safe_expression(expr: str) -> bool:
    stmt = _prepare_statement(expr)
    if stmt is None:
        return True
    if isinstance(stmt, list):
        return all(is_safe_expression(s) for s in stmt)
    if_parts = parse_js_if_statement(stmt)
    if if_parts:
        return _validate_if_parts(if_parts)
    return _validate_normalized_stmt(stmt)


def _validate_if_parts(parts: tuple[str, str]) -> bool:
    cond, body = parts
    return is_safe_expression(cond) and is_safe_expression(body)


def _validate_normalized_stmt(stmt: str) -> bool:
    normalized = normalize_js_expression(stmt)
    if contains_dangerous_pattern(normalized):
        return False
    normalized = _validate_arrow_bodies(normalized)
    if normalized is None:
        return False
    tree = _parse_python(_normalize_js_operators(normalized))
    return tree is not None and is_safe_ast(tree)


def _validate_arrow_bodies(normalized: str) -> str | None:
    normalized, bodies = _extract_arrow_bodies(normalized)
    for b in bodies:
        if b and not is_safe_expression(b):
            return None
    return normalized


def _prepare_statement(expr: str) -> str | list[str] | None:
    expr_stripped = _strip_js_comments(expr).strip()
    if expr_stripped.startswith("return "):
        expr_stripped = expr_stripped[7:].strip()
    statements = split_js_statements(expr_stripped)
    if not statements:
        return None
    if len(statements) > 1:
        return statements
    return statements[0]


def _strip_js_comments(expr: str) -> str:
    expr = re.sub(r"/\*.*?\*/", "", expr, flags=re.DOTALL)
    return re.sub(r"//.*$", "", expr, flags=re.MULTILINE)


def normalize_js_expression(stmt: str) -> str:
    expr = stmt.replace("||", " or ").replace("&&", " and ")
    expr = _normalize_dollar_vars(expr)
    expr = _normalize_js_keywords(expr)
    return expr


def _normalize_dollar_vars(expr: str) -> str:
    expr = re.sub(r"\$(\w+)", r"_asok_\1", expr)
    return re.sub(r"(?<!\w)\$(?!\w)", "_asok_state", expr)


def _normalize_js_keywords(expr: str) -> str:
    expr = re.sub(r"\bnew\s+", "", expr)
    expr = re.sub(r"\b(let|const|var)\s+", "", expr)
    expr = re.sub(r"\btypeof\s+([a-zA-Z0-9_$]+)", r"_asok_typeof(\1)", expr)
    expr = re.sub(r"\btypeof\s*\(", "_asok_typeof(", expr)
    expr = re.sub(
        r"([a-zA-Z0-9_$]+)\s+instanceof\s+([a-zA-Z0-9_$]+)",
        r"_asok_instanceof(\1, \2)",
        expr,
    )
    expr = re.sub(r"\bvoid\s+([a-zA-Z0-9_$]+|\d+)", "None", expr)
    return re.sub(r"\bvoid\s*\(.*?\)", "None", expr)


def contains_dangerous_pattern(expr: str) -> bool:
    return any(re.search(p, expr) for p in _DANGEROUS_PATTERNS)


def _extract_arrow_bodies(expr: str) -> tuple[str, list[str]]:
    parsed_expr, bodies = extract_arrow_functions(expr)
    if not bodies:
        return expr, []
    return parsed_expr, bodies


def _normalize_js_operators(expr: str) -> str:
    expr = expr.replace("===", "==").replace("!==", "!=")
    expr = convert_ternary(expr)
    expr = re.sub(r"!\s*(\w+)", r"not \1", expr)
    expr = re.sub(r"!\s*\(", r"not (", expr)
    expr = re.sub(r"(\w+)\+\+", r"\1 += 1", expr)
    expr = re.sub(r"(\w+)--", r"\1 -= 1", expr)
    expr = re.sub(r"\+\+(\w+)", r"\1 += 1", expr)
    return re.sub(r"--(\w+)", r"\1 -= 1", expr)


def _parse_python(expr: str) -> ast.AST | None:
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError:
        try:
            return ast.parse(expr, mode="exec")
        except SyntaxError:
            return None


def is_safe_ast(tree: ast.AST) -> bool:
    try:
        for node in ast.walk(tree):
            if not _is_node_allowed(node):
                return False
        return True
    except (SyntaxError, ValueError):
        return False


def _is_node_allowed(node: ast.AST) -> bool:
    if type(node) not in _ALLOWED_AST_NODES:
        return False
    if not _are_node_ops_allowed(node):
        return False
    return _is_node_structures_allowed(node)


def _is_node_structures_allowed(node: ast.AST) -> bool:
    if not _is_call_allowed(node):
        return False
    if not _is_name_allowed(node):
        return False
    if not _is_attribute_allowed(node):
        return False
    if not _is_constant_allowed(node):
        return False
    return _is_subscript_allowed(node)


def _is_name_allowed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Name):
        return True
    return node.id not in _FORBIDDEN_NAMES


def _are_node_ops_allowed(node: ast.AST) -> bool:
    if not isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp)):
        return True
    op = getattr(node, "op", None)
    if op is not None and type(op) not in _ALLOWED_AST_OPS:
        return False
    return _check_compare_ops(getattr(node, "ops", None))


def _check_compare_ops(ops) -> bool:
    if ops is None:
        return True
    return all(type(o) in _ALLOWED_AST_OPS for o in ops)


def _is_call_allowed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return True
    if not isinstance(node.func, ast.Name):
        return True
    return node.func.id not in _DANGEROUS_FUNCTIONS


def _is_attribute_allowed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Attribute):
        return True
    attr = node.attr
    if attr in _BLOCKED_JS_PROPERTIES:
        return False
    return not (attr.startswith("__") and attr.endswith("__"))


def _is_constant_allowed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Constant):
        return True
    return _is_constant_val_allowed(node.value)


def _is_constant_val_allowed(val: object) -> bool:
    if not isinstance(val, str):
        return True
    if val in _BLOCKED_JS_PROPERTIES or val in _FORBIDDEN_NAMES:
        return False
    return not (val.startswith("__") and val.endswith("__"))


def _is_str_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _is_string_concat(node: ast.AST) -> bool:
    if not isinstance(node, ast.BinOp):
        return False
    if not isinstance(node.op, ast.Add):
        return False
    return _check_concat_operands(node.left, node.right)


def _check_concat_operands(left: ast.AST, right: ast.AST) -> bool:
    if _is_str_constant(left) or _is_str_constant(right):
        return True
    return _is_string_concat(left) or _is_string_concat(right)


def _is_subscript_allowed(node: ast.AST) -> bool:
    if not isinstance(node, ast.Subscript):
        return True
    slice_node = node.slice
    if isinstance(slice_node, ast.Index):  # Python < 3.9 compatibility
        slice_node = slice_node.value
    if _is_string_concat(slice_node):
        return False
    return True


# ── Async detection ──────────────────────────────────────────────────


@functools.lru_cache(maxsize=2048)
def expression_has_await(expr: str) -> bool:
    try:
        normalized = _strip_js_comments(expr).strip()
        if normalized.startswith("return "):
            normalized = normalized[7:].strip()
        normalized = normalize_js_expression(normalized)
        parsed_expr, _ = extract_arrow_functions(normalized)
        normalized = _normalize_js_operators(parsed_expr)
        tree = _parse_for_await(normalized)
        return any(isinstance(node, ast.Await) for node in ast.walk(tree))
    except Exception:
        return False


def _parse_for_await(expr: str) -> ast.AST:
    try:
        return ast.parse(expr, mode="eval")
    except SyntaxError:
        return ast.parse(expr, mode="exec")
