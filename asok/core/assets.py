from __future__ import annotations

import ast
import functools
import hashlib
import html as _html
import json
import logging
import os
import re
from typing import Any

from ..utils.css import scope_css
from ..utils.js import scope_js
from ..utils.minify import minify_css, minify_js

logger = logging.getLogger("asok.assets")


def _find_outside_char(s: str, target: str) -> int:
    in_quote = None
    escape = False
    i = 0
    while i < len(s):
        char = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == "\\":
            escape = True
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue

        # Check for optional chaining ?. or ?? nullish coalescing
        if target == "?":
            if s[i : i + 2] == "??":
                i += 2
                continue
            if s[i : i + 2] == "?.":
                i += 2
                continue

        if char == target:
            return i
        i += 1
    return -1


def _convert_ternary(s: str) -> str:
    q_idx = _find_outside_char(s, "?")
    if q_idx == -1:
        return s

    # Find matching ':' for the '?' at q_idx
    depth = 1
    c_idx = -1
    in_quote = None
    escape = False
    for i in range(q_idx + 1, len(s)):
        char = s[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            continue

        if char == "?":
            if s[i : i + 2] == "??":
                continue
            if i > 0 and s[i - 1] == "?":
                continue
            if s[i : i + 2] == "?.":
                continue
            depth += 1
        elif char == ":":
            depth -= 1
            if depth == 0:
                c_idx = i
                break

    if c_idx == -1:
        # Unmatched '?', mask to avoid infinite loop
        masked = s[:q_idx] + "\x00" + s[q_idx + 1 :]
        return _convert_ternary(masked).replace("\x00", "?")

    # Find start boundary of the condition cond (cond_start)
    stack = []
    delimiters = {}
    in_quote = None
    escape = False
    for i in range(q_idx):
        char = s[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            continue

        if char in ("(", "[", "{"):
            stack.append((char, i))
        elif char in (")", "]", "}"):
            if stack:
                stack.pop()
        elif char in (",", ";"):
            lvl = len(stack)
            if lvl not in delimiters:
                delimiters[lvl] = []
            delimiters[lvl].append(i)
        elif char == "=":
            # Check if it's a single '=' (not part of '==', '!=', '<=', '>=')
            is_single_eq = True
            if i > 0 and s[i - 1] in ("=", "!", "<", ">"):
                is_single_eq = False
            if i + 1 < len(s) and s[i + 1] == "=":
                is_single_eq = False
            if is_single_eq:
                lvl = len(stack)
                if lvl not in delimiters:
                    delimiters[lvl] = []
                delimiters[lvl].append(i)

    L = len(stack)
    boundary_idx = -1
    if stack:
        boundary_idx = stack[-1][1]
    if L in delimiters and delimiters[L]:
        boundary_idx = max(boundary_idx, delimiters[L][-1])

    cond_start = boundary_idx + 1

    # Find end boundary of expr2 (expr2_end)
    expr2_end = len(s)
    in_quote = None
    escape = False
    for i in range(c_idx + 1, len(s)):
        char = s[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            continue

        if char in ("(", "[", "{"):
            stack.append((char, i))
        elif char in (")", "]", "}"):
            if len(stack) == L:
                expr2_end = i
                break
            if stack:
                stack.pop()
        elif char in (",", ";"):
            if len(stack) == L:
                expr2_end = i
                break
        elif char == "=":
            is_single_eq = True
            if i > 0 and s[i - 1] in ("=", "!", "<", ">"):
                is_single_eq = False
            if i + 1 < len(s) and s[i + 1] == "=":
                is_single_eq = False
            if is_single_eq:
                if len(stack) == L:
                    expr2_end = i
                    break

    cond = s[cond_start:q_idx].strip()
    expr1 = s[q_idx + 1 : c_idx].strip()
    expr2 = s[c_idx + 1 : expr2_end].strip()

    cond_conv = _convert_ternary(cond)
    expr1_conv = _convert_ternary(expr1)
    expr2_conv = _convert_ternary(expr2)

    left = s[:cond_start]
    right = s[expr2_end:]
    reconstructed = (
        f"{left}(({expr1_conv}) if ({cond_conv}) else ({expr2_conv})){right}"
    )

    return _convert_ternary(reconstructed)


def _find_outside_arrow(s: str) -> int:
    in_quote = None
    escape = False
    i = 0
    while i < len(s) - 1:
        char = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == "\\":
            escape = True
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue

        if s[i : i + 2] == "=>":
            return i
        i += 1
    return -1


def _find_matching_paren_forward(s: str, target_close_idx: int) -> int:
    in_quote = None
    escape = False
    stack = []
    i = 0
    while i < len(s):
        char = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == "\\":
            escape = True
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue

        if char == "(":
            stack.append(i)
        elif char == ")":
            if stack:
                open_idx = stack.pop()
                if i == target_close_idx:
                    return open_idx
        i += 1
    return -1


def _find_matching_forward(
    s: str, start_idx: int, open_char: str, close_char: str
) -> int:
    in_quote = None
    escape = False
    depth = 0
    i = start_idx
    while i < len(s):
        char = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == "\\":
            escape = True
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue

        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(s)


def _find_expression_body_end(s: str, start_idx: int) -> int:
    in_quote = None
    escape = False
    depths = {"paren": 0, "bracket": 0, "brace": 0}
    i = start_idx
    while i < len(s):
        char = s[i]
        if escape:
            escape = False
            i += 1
            continue
        if char == "\\":
            escape = True
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            i += 1
            continue

        if char == "(":
            depths["paren"] += 1
        elif char == "[":
            depths["bracket"] += 1
        elif char == "{":
            depths["brace"] += 1
        elif char == ")":
            if depths["paren"] == 0:
                break
            depths["paren"] -= 1
        elif char == "]":
            if depths["bracket"] == 0:
                break
            depths["bracket"] -= 1
        elif char == "}":
            if depths["brace"] == 0:
                break
            depths["brace"] -= 1
        elif char in (",", ";"):
            if all(d == 0 for d in depths.values()):
                break
        i += 1
    return i


def _extract_arrow_functions(s: str) -> tuple[str, list[str]]:
    idx = _find_outside_arrow(s)
    if idx == -1:
        return s, []

    # Find parameter start
    i = idx - 1
    while i >= 0 and s[i].isspace():
        i -= 1

    if i < 0:
        return s.replace("=>", "lambda_dummy:"), []

    param_start = i
    if s[i] == ")":
        param_start = _find_matching_paren_forward(s, i)
        if param_start == -1:
            param_start = i
    else:
        while i >= 0 and (s[i].isalnum() or s[i] in "_$"):
            i -= 1
        param_start = i + 1

    # Find body end
    i = idx + 2
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s):
        return s.replace("=>", "lambda_dummy:"), []

    body_start = i
    if s[i] == "{":
        body_end = _find_matching_forward(s, i, "{", "}")
        body_content = s[body_start + 1 : body_end - 1]
    else:
        body_end = _find_expression_body_end(s, i)
        body_content = s[body_start:body_end]

    left = s[:param_start]
    right = s[body_end:]
    modified_expr = f"{left}None{right}"

    parsed_body, bodies_from_body = _extract_arrow_functions(body_content)
    parsed_modified, bodies_from_modified = _extract_arrow_functions(modified_expr)

    all_bodies = [parsed_body] + bodies_from_body + bodies_from_modified
    return parsed_modified, all_bodies


def _split_js_statements(s: str) -> list[str]:
    parts = []
    current = []
    stack = []
    in_quote = None
    escape = False
    i = 0
    while i < len(s):
        char = s[i]
        if escape:
            escape = False
            current.append(char)
            i += 1
            continue
        if char == "\\":
            escape = True
            current.append(char)
            i += 1
            continue
        if in_quote:
            if char == in_quote:
                in_quote = None
            current.append(char)
            i += 1
            continue
        if char in ("'", '"', "`"):
            in_quote = char
            current.append(char)
            i += 1
            continue
        if char in ("(", "[", "{"):
            stack.append(char)
        elif char in (")", "]", "}"):
            if stack:
                stack.pop()
        elif char == ";" and not stack:
            parts.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    if current:
        parts.append("".join(current).strip())
    return [p for p in parts if p]


def _parse_js_if_statement(s: str) -> tuple[str, str] | None:
    match = re.match(r"^if\s*\(", s)
    if not match:
        return None
    start_paren = match.end() - 1
    end_paren = _find_matching_forward(s, start_paren, "(", ")")
    if end_paren == -1 or end_paren > len(s):
        return None
    cond = s[start_paren + 1 : end_paren - 1].strip()
    body = s[end_paren:].strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1].strip()
    return cond, body


class AssetMixin:
    def get_asset(self, filename: str) -> str:
        """Retrieve an asset file's contents, caching in production."""
        if not hasattr(self, "_asset_cache"):
            self._asset_cache = {}

        debug = self.config.get("DEBUG", False)

        # In production, use the pre-minified version if it exists
        is_pre_minified = False
        if not debug:
            base, ext = os.path.splitext(filename)
            if not base.endswith(".min") and ext in [".js", ".css"]:
                min_filename = f"{base}.min{ext}"
                min_path = os.path.join(
                    os.path.dirname(__file__), "assets", min_filename
                )
                if os.path.exists(min_path):
                    filename = min_filename
                    is_pre_minified = True

        if not debug and filename in self._asset_cache:
            return self._asset_cache[filename]

        path = os.path.join(os.path.dirname(__file__), "assets", filename)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if not debug:
            if not is_pre_minified:
                if filename.endswith(".js"):
                    content = minify_js(content)
                elif filename.endswith(".css"):
                    content = minify_css(content)
            self._asset_cache[filename] = content

        return content

    @staticmethod
    @functools.lru_cache(maxsize=2048)
    def _validate_expression_cached(expr: str) -> bool:
        """Validate that a directive expression is safe (no code injection).

        Uses a hybrid approach: checks for dangerous patterns, then validates structure.
        SECURITY: Prevents code injection via template directives.

        Note: Supports JavaScript syntax since directives execute client-side.
        """
        # Normalize: Remove JS single line and multi-line comments
        expr_stripped = re.sub(r"/\*.*?\*/", "", expr, flags=re.DOTALL)
        expr_stripped = re.sub(r"//.*$", "", expr_stripped, flags=re.MULTILINE)
        expr_stripped = expr_stripped.strip()

        # Handle return statement prefix
        if expr_stripped.startswith("return "):
            expr_stripped = expr_stripped[7:].strip()

        # SECURITY: Split into individual statements first
        statements = _split_js_statements(expr_stripped)
        if len(statements) > 1:
            for stmt in statements:
                if not AssetMixin._validate_expression_cached(stmt):
                    return False
            return True

        if not statements:
            return True

        stmt = statements[0]

        # SECURITY: Check for JavaScript "if" statement
        if_parts = _parse_js_if_statement(stmt)
        if if_parts:
            cond, body = if_parts
            return AssetMixin._validate_expression_cached(
                cond
            ) and AssetMixin._validate_expression_cached(body)

        # Normalize JS logical operators (|| -> or, && -> and) for Python AST parsing compatibility
        normalized_expr = stmt.replace("||", " or ").replace("&&", " and ")

        # Normalize special $ variables for Python AST parsing compatibility
        # Replace $var with _asok_var
        normalized_expr = re.sub(r"\$(\w+)", r"_asok_\1", normalized_expr)
        # Replace standalone $ with _asok_state
        normalized_expr = re.sub(r"(?<!\w)\$(?!\w)", "_asok_state", normalized_expr)

        # Normalize JavaScript 'new' operator (new Date() -> Date()) for Python AST parsing compatibility
        normalized_expr = re.sub(r"\bnew\s+", "", normalized_expr)

        # Normalize JavaScript variable declarations (let/const/var x = 1 -> x = 1)
        normalized_expr = re.sub(r"\b(let|const|var)\s+", "", normalized_expr)

        # Normalize JavaScript 'typeof' operator (typeof x -> _asok_typeof(x))
        normalized_expr = re.sub(
            r"\btypeof\s+([a-zA-Z0-9_$]+)", r"_asok_typeof(\1)", normalized_expr
        )
        normalized_expr = re.sub(r"\btypeof\s*\(", "_asok_typeof(", normalized_expr)

        # Normalize JavaScript 'instanceof' operator (x instanceof Y -> _asok_instanceof(x, Y))
        normalized_expr = re.sub(
            r"([a-zA-Z0-9_$]+)\s+instanceof\s+([a-zA-Z0-9_$]+)",
            r"_asok_instanceof(\1, \2)",
            normalized_expr,
        )

        # Normalize JavaScript 'void' operator (void 0 -> None)
        normalized_expr = re.sub(
            r"\bvoid\s+([a-zA-Z0-9_$]+|\d+)", "None", normalized_expr
        )
        normalized_expr = re.sub(r"\bvoid\s*\(.*?\)", "None", normalized_expr)

        # SECURITY: Check for dangerous keywords first (server-side injection attempt)
        DANGEROUS_PATTERNS = [
            # Python server-side injection
            r"\b__import__\b",
            r"\beval\b",
            r"\bexec\b",
            r"\bcompile\b",
            r"\bopen\b\s*\(",
            r"\bfile\b\s*\(",
            r"\b__\w+__\b",  # Dunder methods/attributes
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
            r'\bfetch\s*\(\s*[\'"]https?://',  # fetch with absolute URL
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
            # Constructor-based bypasses (eval alternatives)
            r"\bconstructor\.constructor\b",  # constructor.constructor or .constructor.constructor
            r'\bconstructor\s*\[\s*[\'"]constructor[\'"]\s*\]',
            r'\[\s*[\'"]constructor[\'"]\s*\]\s*\[\s*[\'"]constructor[\'"]\s*\]',
            r"\.concat\.constructor\b",
            r"\bFunction\s*\(",  # Function constructor
            r"\.prototype\b",
            # Template literals with interpolation
            r"`.*\$\{.*\}.*`",
            # Extra client-side security restrictions
            r"\blocalStorage\b",
            r"\bsessionStorage\b",
            r"\bdocument\.cookie\b",
            r"\bindexedDB\b",
            r"\bWebSocket\b",
            r"\bEventSource\b",
            r"\bpostMessage\b",
            r"\balert\b",
        ]

        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, normalized_expr):
                return False

        # For arrow functions, extract and validate their bodies recursively
        parsed_expr, all_bodies = _extract_arrow_functions(normalized_expr)
        if all_bodies:
            for body in all_bodies:
                if body and not AssetMixin._validate_expression_cached(body):
                    return False
            normalized_expr = parsed_expr

        # Handle JavaScript equality operators
        normalized_expr = normalized_expr.replace("===", "==")
        normalized_expr = normalized_expr.replace("!==", "!=")

        # Convert JS ternary operators to Python 'if-else' expressions
        normalized_expr = _convert_ternary(normalized_expr)

        # Handle JavaScript NOT operator (! -> not)
        # Must be done before ++ and -- to avoid conflicts
        normalized_expr = re.sub(r"!\s*(\w+)", r"not \1", normalized_expr)
        normalized_expr = re.sub(r"!\s*\(", r"not (", normalized_expr)

        # Handle JavaScript increment/decrement operators
        normalized_expr = re.sub(r"(\w+)\+\+", r"\1 += 1", normalized_expr)
        normalized_expr = re.sub(r"(\w+)--", r"\1 -= 1", normalized_expr)
        normalized_expr = re.sub(r"\+\+(\w+)", r"\1 += 1", normalized_expr)
        normalized_expr = re.sub(r"--(\w+)", r"\1 -= 1", normalized_expr)

        # Whitelist of allowed AST node types
        ALLOWED_NODES = {
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

        ALLOWED_OPS = {
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

        FORBIDDEN_NAMES = {
            "eval",
            "exec",
            "compile",
            "__import__",
            "__builtins__",
            "__dict__",
            "__class__",
            "__bases__",
            "__subclasses__",
        }

        try:
            try:
                tree = ast.parse(normalized_expr, mode="eval")
            except SyntaxError:
                try:
                    tree = ast.parse(normalized_expr, mode="exec")
                except SyntaxError:
                    # SECURITY: Reject expressions with syntax errors instead of allowing them
                    return False

            for node in ast.walk(tree):
                node_type = type(node)
                if node_type not in ALLOWED_NODES:
                    return False

                if isinstance(node, (ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp)):
                    if hasattr(node, "op"):
                        if type(node.op) not in ALLOWED_OPS:
                            return False
                    if hasattr(node, "ops"):
                        for op in node.ops:
                            if type(op) not in ALLOWED_OPS:
                                return False

                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        func_name = node.func.id
                        DANGEROUS_FUNCTIONS = {
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
                        if func_name in DANGEROUS_FUNCTIONS:
                            return False

                if isinstance(node, ast.Name):
                    if node.id in FORBIDDEN_NAMES:
                        return False

                if isinstance(node, ast.Attribute):
                    if node.attr.startswith("__") and node.attr.endswith("__"):
                        return False

            return True

        except (SyntaxError, ValueError):
            return False

    def _validate_directive_expression(self, expr: str) -> bool:
        """Validate that a directive expression is safe (no code injection).

        Uses a hybrid approach: checks for dangerous patterns, then validates structure.
        SECURITY: Prevents code injection via template directives.

        Note: Supports JavaScript syntax since directives execute client-side.
        """
        return self._validate_expression_cached(expr)

    @staticmethod
    @functools.lru_cache(maxsize=2048)
    def _is_async_expression_cached(expr: str) -> bool:
        """Determines if a JS/Python-like expression uses an await statement (cached)."""
        try:
            normalized_expr = expr.strip()
            # Clean comments first so we don't trip over comments
            normalized_expr = re.sub(r"/\*.*?\*/", "", normalized_expr, flags=re.DOTALL)
            normalized_expr = re.sub(r"//.*$", "", normalized_expr, flags=re.MULTILINE)
            normalized_expr = normalized_expr.strip()

            # Handle return statement prefix
            if normalized_expr.startswith("return "):
                normalized_expr = normalized_expr[7:].strip()

            normalized_expr = normalized_expr.replace("||", " or ").replace(
                "&&", " and "
            )
            normalized_expr = re.sub(r"\$(\w+)", r"_asok_\1", normalized_expr)
            normalized_expr = re.sub(r"(?<!\w)\$(?!\w)", "_asok_state", normalized_expr)

            # Extract arrow functions
            parsed_expr, _ = _extract_arrow_functions(normalized_expr)
            normalized_expr = parsed_expr

            normalized_expr = normalized_expr.replace("===", "==").replace("!==", "!=")
            normalized_expr = _convert_ternary(normalized_expr)
            normalized_expr = re.sub(r"!\s*(\w+)", r"not \1", normalized_expr)
            normalized_expr = re.sub(r"!\s*\(", r"not (", normalized_expr)
            normalized_expr = re.sub(r"(\w+)\+\+", r"\1 += 1", normalized_expr)
            normalized_expr = re.sub(r"(\w+)--", r"\1 -= 1", normalized_expr)
            normalized_expr = re.sub(r"\+\+(\w+)", r"\1 += 1", normalized_expr)
            normalized_expr = re.sub(r"--(\w+)", r"\1 -= 1", normalized_expr)

            try:
                tree = ast.parse(normalized_expr, mode="eval")
            except SyntaxError:
                tree = ast.parse(normalized_expr, mode="exec")
            return any(isinstance(node, ast.Await) for node in ast.walk(tree))
        except Exception:
            return False

    def _precompile_directives(self, html: str) -> tuple[str, dict[str, str]]:
        """Pre-compile Asok directives into a hash-based registry for CSP Zero-Eval security."""
        registry = {}

        expr_attrs = {
            "asok-text",
            "asok-html",
            "asok-show",
            "asok-hide",
            "asok-if",
            "asok-elif",
            "asok-state",
            "asok-init",
            "asok-fetch-async",
        }
        prefixes = ["asok-on:", "asok-class:", "asok-bind:"]

        def get_hash(expr: str) -> str:
            return hashlib.md5(expr.strip().encode()).hexdigest()[:12]

        def replacer(match):
            name = match.group(1)
            val = _html.unescape(match.group(3))

            if name.endswith("-ref"):
                return match.group(0)

            if name == "asok-for":
                if " in " in val:
                    var_part, expr_part = val.split(" in ", 1)
                    if not self._validate_directive_expression(expr_part):
                        raise ValueError(
                            f"SECURITY: Unsafe expression in {name}: '{expr_part}'. "
                            f"Only safe Python expressions are allowed in directives."
                        )
                    h = get_hash(expr_part)
                    registry[h] = expr_part
                    return f'asok-for-ref="{h}" asok-for-var="{var_part.strip()}"'
                return match.group(0)

            is_expr = name in expr_attrs
            if not is_expr:
                if name == "asok-class":
                    is_expr = True
                else:
                    for p in prefixes:
                        if name.startswith(p):
                            is_expr = True
                            break

            if is_expr:
                if not self._validate_directive_expression(val):
                    raise ValueError(
                        f"SECURITY: Unsafe expression in {name}: '{val}'. "
                        f"Only safe Python expressions are allowed in directives. "
                        f"Forbidden: eval(), exec(), __import__(), dunder methods, etc."
                    )
                h = get_hash(val)
                registry[h] = val
                if ":" in name:
                    parts = name.split(":", 1)
                    return f'{parts[0]}-ref:{parts[1]}="{h}"'
                return f'{name}-ref="{h}"'

            return match.group(0)

        new_html = re.sub(
            r'(?<![a-zA-Z0-9-])(asok-[a-zA-Z0-9:.\-]+)=([\'"])(.*?)\2',
            replacer,
            html,
            flags=re.DOTALL,
        )

        return new_html, registry

    def _inject_assets(
        self,
        content: str,
        request: Any,
        nonce: str,
        stream: bool = False,
        include_scripts: bool = True,
        only_scripts: bool = False,
    ) -> str:
        """Inject required CSRF tags, metadata, and scripts into the HTML response."""
        if not isinstance(content, str):
            return content

        if not nonce or not isinstance(nonce, str) or len(nonce) < 10:
            nonce = request.nonce

        request._nonce = nonce

        if not hasattr(request, "_asok_pending_scripts"):
            request._asok_pending_scripts = ""

        if not hasattr(request, "_asok_pending_styles"):
            request._asok_pending_styles = ""

        # 0. SEO Metadata (Title, Metas, Links)
        meta_html = ""
        if not only_scripts and not getattr(request, "_asok_meta_done", False):
            meta_obj = getattr(request, "meta", None)
            if meta_obj:
                if meta_obj._title:
                    if "<title>" in content.lower():
                        start = content.lower().find("<title>")
                        end = content.lower().find("</title>", start)
                        if end != -1:
                            content = content[:start] + content[end + 8 :]

                    meta_html += (
                        f"    <title>{_html.escape(str(meta_obj._title))}</title>\n"
                    )

            if meta_obj and meta_obj._description:
                content = re.sub(
                    r'<meta\s+name=["\']description["\']\s+content=["\'].*?["\']\s*/?>',
                    "",
                    content,
                    flags=re.IGNORECASE,
                )
                meta_html += f'    <meta name="description" content="{_html.escape(str(meta_obj._description))}">'
                meta_html += "\n"

            if meta_obj:
                for item in meta_obj._items:
                    itype, ikey, ival, ikwargs = item
                    if itype == "name":
                        if ikey.lower() == "description" and meta_obj._description:
                            continue
                        meta_html += f'    <meta name="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">'
                    elif itype == "property":
                        meta_html += f'    <meta property="{_html.escape(ikey)}" content="{_html.escape(str(ival))}">'
                    elif itype == "link":
                        extra = " ".join(
                            f'{k}="{_html.escape(str(v))}"' for k, v in ikwargs.items()
                        )
                        meta_html += f'    <link rel="{_html.escape(ikey)}" href="{_html.escape(ival)}" {extra}>'
                    meta_html += "\n"

            if meta_html:
                request._asok_meta_done = True

        if meta_html:
            if "<head>" in content:
                content = content.replace("<head>", "<head>\n" + meta_html, 1)
            elif "<head " in content:
                idx = content.find("<head ")
                end = content.find(">", idx)
                if end != -1:
                    content = content[: end + 1] + "\n" + meta_html + content[end + 1 :]

        # 0.5 Scoped Assets (CSS/JS) and Page ID
        page_id = getattr(request, "page_id", "unknown")
        if request.page_id:
            if not getattr(request, "_asok_page_id_done", False):
                if "<body" in content:
                    if 'data-page-id="' not in content:
                        content = content.replace(
                            "<body", f'<body data-page-id="{page_id}"', 1
                        )
                    else:
                        content = re.sub(
                            r'data-page-id="[^"]*"',
                            f'data-page-id="{page_id}"',
                            content,
                            1,
                        )

                if stream:
                    marker = f"<!-- page-id:{page_id} -->\n"
                    if "</body>" in content.lower():

                        def inject_marker(m):
                            return marker + m.group(1)

                        content = re.sub(
                            r"(</body>)", inject_marker, content, flags=re.I, count=1
                        )
                        request._asok_page_id_done = True
                    else:
                        content += marker
                        request._asok_page_id_done = True

            # 2. Inject Scoped CSS
            if not getattr(request, "_asok_css_done", False):
                if request.scoped_assets.get("css"):
                    try:
                        with open(
                            request.scoped_assets["css"], "r", encoding="utf-8"
                        ) as f:
                            raw_css = f.read()

                        scoped_css_content = scope_css(raw_css, page_id)
                        if not self.config.get("DEBUG") and not self.config.get(
                            "ASOK_BUILD"
                        ):
                            scoped_css_content = minify_css(scoped_css_content)
                        safe_page_id = _html.escape(page_id, quote=True)
                        safe_css = scoped_css_content.replace("</style>", "<\\/style>")
                        style_tag = f'\n<style id="asok-scoped-css" data-page-id="{safe_page_id}">\n{safe_css}\n</style>\n'

                        if "</head>" in content.lower():

                            def inject_css(m):
                                return style_tag + m.group(1)

                            content = re.sub(
                                r"(</head>)", inject_css, content, flags=re.I, count=1
                            )
                            request._asok_css_done = True
                        else:
                            content = style_tag + content
                            request._asok_css_done = True
                    except Exception:
                        pass

            # 3. Inject Scoped JS
            if not getattr(request, "_asok_js_done", False):
                if request.scoped_assets.get("js"):
                    try:
                        with open(
                            request.scoped_assets["js"], "r", encoding="utf-8"
                        ) as f:
                            raw_js = f.read()
                        scoped_js_content = scope_js(raw_js)
                        if not self.config.get("DEBUG") and not self.config.get(
                            "ASOK_BUILD"
                        ):
                            scoped_js_content = minify_js(scoped_js_content)
                        safe_js = scoped_js_content.replace("</script>", "<\\/script>")
                        request._asok_pending_scripts += (
                            f'\n<script id="asok-scoped-js" nonce="{nonce}">'
                            "(function(){"
                            "const init=function(){" + safe_js + "};"
                            "if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);"
                            "else init();"
                            "})()"
                            "</script>\n"
                        )
                        request._asok_js_done = True
                    except Exception:
                        pass

        # 4. Final Injection of accumulated styles
        styles = request._asok_pending_styles
        if styles and not getattr(request, "_asok_styles_done", False):
            if "</head>" in content.lower():
                request._asok_styles_done = True
                request._asok_pending_styles = ""

                def inject_styles(m):
                    return styles + m.group(1)

                content = re.sub(
                    r"(</head>)", inject_styles, content, flags=re.I, count=1
                )
            elif not stream:
                request._asok_styles_done = True
                request._asok_pending_styles = ""
                content = styles + content

        # [END OF ASSET INJECTION]
        if not only_scripts and not getattr(request, "_asok_csrf_done", False):
            csrf_meta = f'<meta name="csrf-token" content="{getattr(request, "csrf_token_value", "")}">'
            if "<head>" in content.lower():

                def inject_csrf(m):
                    return m.group(1) + "\n" + csrf_meta

                content = re.sub(
                    r"(<head.*?>)", inject_csrf, content, flags=re.I, count=1
                )
                request._asok_csrf_done = True

        # 1.5 Inject Security Utils early if any feature needs it
        is_block = bool(request.environ.get("HTTP_X_BLOCK"))
        needs_any_js_feature = (
            not is_block
            and not getattr(request, "_asok_security_utils_done", False)
            and (
                "asok-transition" in content
                or any(
                    attr in content
                    for attr in ["data-block", "data-sse", "data-url", "data-method"]
                )
                or ("data-asok-component" in content or "ws-" in content)
                or any(
                    attr in content
                    for attr in [
                        "asok-state",
                        "asok-on:",
                        "asok-text",
                        "asok-show",
                        "asok-hide",
                        "asok-class:",
                        "asok-bind:",
                        "asok-model",
                        "asok-if",
                        "asok-for",
                    ]
                )
            )
        )

        if needs_any_js_feature:
            request._asok_security_utils_done = True
            security_utils_js = self.get_asset("asok_security_utils.min.js")
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n{security_utils_js}\n</script>\n'
            )

        # 2. Asok Transitions
        needs_transition = (
            "asok-transition" in content
            and not is_block
            and not getattr(request, "_asok_transition_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_transition_done", False)
        ):
            needs_transition = True

        if needs_transition:
            request._asok_transition_done = True
            if not hasattr(request, "_asok_pending_styles"):
                request._asok_pending_styles = ""

            transitions_css = self.get_asset("asok_transitions.min.css")
            request._asok_pending_styles += f'<style id="asok-transitions" nonce="{nonce}">{transitions_css}</style>\n'

            transitions_js = self.get_asset("asok_transitions.min.js")
            request._asok_pending_scripts += f'<script id="asok-transition-engine" nonce="{nonce}">{transitions_js}</script>\n'

        needs_reactive = (
            any(
                attr in content
                for attr in ["data-block", "data-sse", "data-url", "data-method"]
            )
            and not is_block
            and not getattr(request, "_asok_reactive_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_reactive_done", False)
        ):
            needs_reactive = True

        if needs_reactive:
            request._asok_reactive_done = True
            spa_js = self.get_asset("asok_spa.min.js")
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n{spa_js}\n</script>'
            )

        needs_alive = (
            ("data-asok-component" in content or "ws-" in content)
            and not is_block
            and not getattr(request, "_asok_alive_done", False)
        )
        if (
            stream
            and only_scripts
            and not is_block
            and not getattr(request, "_asok_alive_done", False)
        ):
            needs_alive = True

        if needs_alive:
            request._asok_alive_done = True
            ws_port = self.config.get("WS_PORT", 8001)
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">window.ASOK_WS_PORT = {ws_port};</script>\n'
            )
            alive_js = self.get_asset("asok_alive.min.js")
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">\n{alive_js}\n</script>\n'
            )

        # 2. Inject nonce into all existing <script>, <style>, and <link> tags
        def inject_nonce_attr(m):
            tag = m.group(1)
            attrs = m.group(2)
            if 'nonce="' in attrs.lower():
                return re.sub(r'(?i)nonce=".*?"', f'nonce="{nonce}"', m.group(0))
            return f'<{tag}{attrs} nonce="{nonce}">'

        content = re.sub(
            r"<(script|style|link)\b([^>]*?)>",
            inject_nonce_attr,
            content,
            flags=re.IGNORECASE,
        )

        # 3. Handle directives asset injection
        registry = {}
        # Check for precompiled directives registry
        debug = self.config.get("DEBUG", False)
        registry_file = os.path.join(self._partials_path, "js", "directives_registry.js")
        has_precompiled_registry = not debug and os.path.exists(registry_file)

        needs_directives = any(
            attr in content
            for attr in [
                "asok-state",
                "asok-on:",
                "asok-text",
                "asok-show",
                "asok-hide",
                "asok-class:",
                "asok-bind:",
                "asok-model",
                "asok-if",
                "asok-for",
                "asok-init",
                "asok-ref",
                "asok-teleport",
                "asok-cloak",
                "asok-fetch",
                "asok-fetch-async",
                "asok-toggle",
                # Support precompiled versions
                "asok-state-ref",
                "asok-on-ref:",
                "asok-text-ref",
                "asok-show-ref",
                "asok-hide-ref",
                "asok-class-ref:",
                "asok-bind-ref:",
                "asok-model-ref",
                "asok-if-ref",
                "asok-for-ref",
                "asok-init-ref",
                "asok-fetch-async-ref",
            ]
        ) or getattr(request, "_asok_needs_directives", False)

        if needs_directives:
            if has_precompiled_registry:
                # Bypass runtime precompilation
                if getattr(request, "_asok_directives_done", False) or is_block:
                    # For block updates, registry.js is already loaded globally,
                    # and the directives runner will scan the updated block.
                    # Nothing to do!
                    pass
                else:
                    request._asok_directives_done = True
                    directives_css = self.get_asset("asok_directives.min.css")
                    request._asok_pending_styles += (
                        f'<style nonce="{nonce}">{directives_css}</style>'
                    )

                    registry_url = "/js/directives_registry.js"
                    h = self._static_hash("js/directives_registry.js")
                    if h:
                        registry_url += f"?v={h}"

                    directives_js = self.get_asset("asok_directives.min.js")
                    request._asok_pending_scripts += (
                        f'<script nonce="{nonce}">\n'
                        f'window.Asok = window.Asok || {{}}; window.Asok.nonce = "{nonce}";\n'
                        f"</script>\n"
                        f'<script src="{registry_url}" nonce="{nonce}"></script>\n'
                        f'<script nonce="{nonce}">\n'
                        f"{directives_js}\n"
                        f"</script>"
                    )
            else:
                content, registry = self._precompile_directives(content)

                registry_js = ""
                if registry:
                    registry_entries = []
                    for h, expr in registry.items():
                        is_stmt = (
                            ";" in expr
                            or "return " in expr
                            or bool(
                                re.search(
                                    r"\b(if|for|while|const|let|var|function)\b", expr
                                )
                            )
                        )
                        if expr.strip().startswith("{") and not is_stmt:
                            expr = f"({expr})"

                        body = f"return ({expr})" if not is_stmt else expr
                        body = re.sub(r"\s+", " ", body).strip()

                        # Check if the expression contains 'await' keyword
                        is_async = self._is_async_expression_cached(expr)

                        fn_prefix = "async " if is_async else ""
                        registry_entries.append(
                            f"    {json.dumps(h)}: {fn_prefix}function($, $store, $el, $event, $refs, $nextTick) {{ with($||{{}}) {{ {body} }} }}"
                        )
                    registry_js = (
                        "window.__asok_registry = Object.assign(window.__asok_registry || {}, {\n"
                        + ",\n".join(registry_entries)
                        + "\n});\n"
                    )

                if getattr(request, "_asok_directives_done", False) or is_block:
                    if registry_js:
                        request._asok_pending_scripts += (
                            f'<script nonce="{nonce}">\n{registry_js}</script>\n'
                        )
                else:
                    request._asok_directives_done = True

                    directives_css = self.get_asset("asok_directives.min.css")
                    request._asok_pending_styles += (
                        f'<style nonce="{nonce}">{directives_css}</style>'
                    )

                    directives_js = self.get_asset("asok_directives.min.js")
                    request._asok_pending_scripts += (
                        f'<script nonce="{nonce}">\n'
                        f'window.Asok = window.Asok || {{}}; window.Asok.nonce = "{nonce}";\n'
                        f"{registry_js}\n"
                        f"{directives_js}\n"
                        f"</script>"
                    )

        # 3.5 Handle widgets asset injection
        markers = ["Asok.", "asok-dropdown", "asok-table", "asok-toggle", "asok-badge", "asok-pagination"]

        precompiled_uses_widgets = False
        if has_precompiled_registry:
            if not hasattr(self, "_precompiled_uses_widgets"):
                self._precompiled_uses_widgets = False
                try:
                    if os.path.exists(registry_file):
                        with open(registry_file, "r", encoding="utf-8") as f:
                            registry_content = f.read()
                        self._precompiled_uses_widgets = any(marker in registry_content for marker in markers)
                except Exception:
                    pass
            precompiled_uses_widgets = self._precompiled_uses_widgets

        needs_widgets = (
            (any(marker in content for marker in markers) or
             (has_precompiled_registry and precompiled_uses_widgets) or
             (not has_precompiled_registry and any(any(marker in val for marker in markers) for val in registry.values())))
            and not getattr(request, "_asok_widgets_done", False)
        )
        if needs_widgets:
            request._asok_widgets_done = True
            try:
                widgets_js = self.get_asset("asok_widgets.min.js")
                request._asok_pending_scripts += (
                    f'<script nonce="{nonce}">\n{widgets_js}\n</script>\n'
                )
            except Exception:
                pass
            try:
                widgets_css = self.get_asset("asok_widgets.min.css")
                request._asok_pending_styles += (
                    f'<style nonce="{nonce}">{widgets_css}</style>\n'
                )
            except Exception:
                pass

        # 6. Live Reload (DEBUG only)
        if (
            self.config.get("DEBUG")
            and not is_block
            and not getattr(request, "_asok_reload_done", False)
        ):
            request._asok_reload_done = True
            reload_js = self.get_asset("asok_reload.min.js")
            request._asok_pending_scripts += (
                f'<script nonce="{nonce}">{reload_js}</script>'
            )

        # Final Injection of accumulated styles
        if not is_block:
            styles = request._asok_pending_styles
            if styles:
                if "</head>" in content.lower():
                    request._asok_pending_styles = ""

                    def inject_styles(m):
                        return styles + m.group(1)

                    content = re.sub(
                        r"(</head>)", inject_styles, content, flags=re.I, count=1
                    )
                elif not stream:
                    request._asok_pending_styles = ""
                    content = styles + content

        # Final Injection of accumulated scripts
        scripts = request._asok_pending_scripts
        if scripts and not getattr(request, "_asok_scripts_done", False):
            if "</body>" in content.lower():
                request._asok_scripts_done = True
                request._asok_pending_scripts = ""

                def inject_scripts(m):
                    return scripts + m.group(1)

                content = re.sub(
                    r"(</body>)", inject_scripts, content, flags=re.I, count=1
                )

            elif not stream or is_block:
                is_end = (
                    "</html>" in content.lower() or "</template>" in content.lower()
                )
                if is_end:
                    request._asok_scripts_done = True
                    request._asok_pending_scripts = ""
                    content = content + "\n" + scripts
                else:
                    stripped = content.strip()
                    inside_tag = re.search(r"<[^>]*$", content)
                    is_continuation = (
                        stripped and not stripped.startswith("<") and ">" in stripped
                    )

                    request._asok_scripts_done = True
                    request._asok_pending_scripts = ""

                    if inside_tag or is_continuation:
                        content = scripts + content
                    else:
                        content = content + "\n" + scripts

        # Developer Toolbar (Optional)
        def is_true(val):
            if isinstance(val, str):
                return val.lower() in ("true", "yes", "1", "on")
            return bool(val)

        show_toolbar = is_true(self.config.get("TOOLBAR"))
        if "TOOLBAR" not in self.config:
            show_toolbar = is_true(self.config.get("DEBUG"))

        if show_toolbar and not is_block:
            if "</html>" in content.lower() or "</body>" in content.lower():
                try:
                    from ..toolbar import DeveloperToolbar

                    toolbar = DeveloperToolbar(request, self)
                    content = toolbar.inject(content)
                except ImportError as e:
                    logger.debug(f"Toolbar import failed: {e}")
                except Exception as e:
                    logger.error(f"Toolbar injection failed: {e}", exc_info=True)

        return content
