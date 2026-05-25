from __future__ import annotations

import hashlib
import secrets
from typing import Any, Callable, Optional

from asok.cache import default_cache
from asok.exceptions import TemplateError

from .filters import TEMPLATE_FILTERS
from .loop import _Loop
from .preprocessor import _RE_TOKENS, _macro_cache, _macro_mtimes
from .resolver import _resolve_expr, _resolve_expr_full
from .safestring import _escape
from .sandbox import _get, _resolve_name
from .tests import TEMPLATE_TESTS

# Caches
_compiled_cache: dict[str, Callable[..., Any]] = {}  # hash(template_string) -> callable
_dotted_cache: dict[str, Any] = {}  # expr -> resolved expr


def clear_template_caches() -> None:
    """Clear all template caches. Useful in development mode."""
    _compiled_cache.clear()
    _dotted_cache.clear()
    _macro_cache.clear()
    _macro_mtimes.clear()


def _compile_and_run(
    template_string: str, context: dict[str, Any], is_debug: bool = False
) -> Any:
    """Compile a pre-processed template string and execute it."""
    cache_key = hashlib.md5(template_string.encode()).hexdigest()
    if is_debug:
        cache_key += "_debug"
    run_fn = _compiled_cache.get(cache_key)

    if run_fn is None:
        tokens = _RE_TOKENS.split(template_string)
        code = [
            "def __run_template(context, __filters, __tests, _get, _res, _debug):",
            "    pass",
        ]
        indent = 4
        # track defined local variables per level (stack)
        local_scope_stack: list[set[str]] = [
            {"context", "__filters", "__tests", "_get", "_res", "_debug"}
        ]
        block_stack = []

        def get_all_locals() -> set[str]:
            s = set()
            for stack_level in local_scope_stack:
                s.update(stack_level)
            return s

        def _get_capture_var() -> Optional[str]:
            """Get the capture variable if we're in a set, call, or cache block."""
            for block_type, block_data in reversed(block_stack):
                if block_type == "set":
                    return block_data[1]  # (var_name, capture_var)
                elif block_type == "call":
                    return block_data[
                        2
                    ]  # (macro_name, args_part, capture_var, caller_var)
                elif block_type == "cache":
                    return block_data[0]  # cache_capture_var
            return None

        for token in tokens:
            if token.startswith("{{"):
                expr = token[2:-2].strip().lstrip("-").rstrip("-").strip()
                # Normalize newlines to spaces for valid Python syntax
                expr = " ".join(expr.split())
                resolved = _resolve_expr_full(expr, get_all_locals(), is_debug)
                # Check if we're capturing content for a set or call block
                capture_var = _get_capture_var()
                if capture_var:
                    code.append(
                        " " * indent + f"{capture_var}.append(_escape({resolved}))"
                    )
                else:
                    code.append(" " * indent + f"yield _escape({resolved})")
            elif token.startswith("{%"):
                stmt = token[2:-2].strip().lstrip("-").rstrip("-").strip()
                if stmt.startswith("set "):
                    rest = stmt[4:].strip()
                    # Check if it's a block assignment (no =) or inline (has =)
                    if "=" in rest:
                        # Inline: {% set var = value %}
                        var_name, expr = rest.split("=", 1)
                        var_name = var_name.strip()
                        local_scope_stack[-1].add(var_name)
                        code.append(
                            " " * indent
                            + f"{var_name} = {_resolve_expr_full(expr.strip(), get_all_locals(), is_debug)}"
                        )
                    else:
                        # Block: {% set varname %}...{% endset %}
                        var_name = rest.strip()
                        local_scope_stack[-1].add(var_name)
                        capture_var = f"__set_capture_{secrets.token_hex(4)}"
                        block_stack.append(("set", (var_name, capture_var)))
                        # Start capturing by appending to a list
                        code.append(f"{' ' * indent}{capture_var} = []")
                        code.append(f"{' ' * indent}if True:  # set block scope")
                        indent += 4
                        local_scope_stack.append({capture_var})
                elif stmt.startswith("do "):
                    # {% do expr %} - execute expression without outputting
                    expr = stmt[3:].strip()
                    resolved = _resolve_expr_full(expr, get_all_locals(), is_debug)
                    code.append(" " * indent + f"{resolved}")
                elif stmt == "break":
                    # {% break %} - break out of loop
                    code.append(" " * indent + "break")
                elif stmt == "continue":
                    # {% continue %} - continue to next iteration
                    code.append(" " * indent + "continue")
                elif stmt.startswith("cache "):
                    # {% cache "key" ttl %}
                    parts = stmt[6:].strip().split(maxsplit=1)
                    key_expr = parts[0]
                    ttl_expr = parts[1] if len(parts) > 1 else "None"

                    key_resolved = _resolve_expr_full(
                        key_expr, get_all_locals(), is_debug
                    )
                    ttl_resolved = _resolve_expr_full(
                        ttl_expr, get_all_locals(), is_debug
                    )
                    cache_id = secrets.token_hex(4)
                    cache_capture_var = f"__cache_capture_{cache_id}"

                    block_stack.append(
                        (
                            "cache",
                            (cache_capture_var, key_resolved, ttl_resolved, cache_id),
                        )
                    )

                    code.append(
                        f"{' ' * indent}__cache_val_{cache_id} = default_cache.get({key_resolved})"
                    )
                    code.append(f"{' ' * indent}if __cache_val_{cache_id} is not None:")
                    code.append(f"{' ' * (indent + 4)}yield __cache_val_{cache_id}")
                    code.append(f"{' ' * indent}else:")
                    indent += 4
                    code.append(f"{' ' * indent}{cache_capture_var} = []")
                    local_scope_stack.append({cache_capture_var})
                elif stmt.startswith("call "):
                    # {% call macro_expr %}...{% endcall %}
                    # Captures block content and passes it as caller() to the macro
                    macro_call = stmt[5:].strip()

                    # Parse macro_call to extract name and arguments
                    # E.g., "card('Test')" -> name="card", args="'Test'"
                    if "(" in macro_call:
                        macro_name, args_part = macro_call.split("(", 1)
                        macro_name = macro_name.strip()
                        args_part = args_part.rstrip(")")
                    else:
                        macro_name = macro_call
                        args_part = ""

                    # Resolve the macro name
                    resolved_name = _resolve_expr_full(
                        macro_name, get_all_locals(), is_debug
                    )

                    # Create a capture variable for the block content
                    capture_var = f"__capture_{secrets.token_hex(4)}"
                    caller_var = f"__caller_{secrets.token_hex(4)}"
                    block_stack.append(
                        ("call", (resolved_name, args_part, capture_var, caller_var))
                    )
                    code.append(f"{' ' * indent}{capture_var} = []")
                    code.append(f"{' ' * indent}if True:  # call block scope")
                    indent += 4
                    local_scope_stack.append({capture_var, caller_var})
                elif stmt.startswith("with "):
                    # {% with x = expr %} or {% with x=1, y=2, z=3 %}
                    var_part = stmt[5:].strip()
                    if "=" in var_part:
                        # Create a new scope for with block
                        block_stack.append(("with", None))
                        new_scope = set()
                        # Support multiple assignments: x=1, y=2, z=3
                        assignments = [a.strip() for a in var_part.split(",")]
                        for assignment in assignments:
                            if "=" in assignment:
                                var_name, expr = assignment.split("=", 1)
                                var_name = var_name.strip()
                                new_scope.add(var_name)
                                code.append(
                                    " " * indent
                                    + f"{var_name} = {_resolve_expr_full(expr.strip(), get_all_locals(), is_debug)}"
                                )
                        # Create a dummy if block to maintain scope
                        code.append(f"{' ' * indent}if True:")
                        indent += 4
                        local_scope_stack.append(new_scope)
                elif (
                    stmt.startswith("for ")
                    or stmt.startswith("if ")
                    or stmt.startswith("elif ")
                    or stmt.startswith("else")
                ):
                    if stmt.startswith("elif ") or stmt.startswith("else"):
                        if block_stack and block_stack[-1][0] in ("if", "for"):
                            indent -= 4
                            local_scope_stack.pop()
                            local_scope_stack.append(set())
                        elif is_debug:
                            code.append(
                                f"{' ' * indent}# Warning: {stmt} outside of block"
                            )

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
                elif stmt in [
                    "endif",
                    "endfor",
                    "endset",
                    "endwith",
                    "endcall",
                    "endcache",
                ]:
                    if block_stack:
                        block_type, block_data = block_stack.pop()

                        # Special handling for capture blocks (set, call, cache)
                        if block_type == "cache" and stmt == "endcache":
                            cache_capture_var, key_resolved, ttl_resolved, cache_id = (
                                block_data
                            )
                            code.append(
                                f"{' ' * indent}__cache_output_{cache_id} = ''.join({cache_capture_var})"
                            )
                            code.append(
                                f"{' ' * indent}default_cache.set({key_resolved}, __cache_output_{cache_id}, ttl={ttl_resolved})"
                            )
                            code.append(
                                f"{' ' * indent}yield __cache_output_{cache_id}"
                            )
                            indent -= 4
                            local_scope_stack.pop()
                        elif block_type == "set" and stmt == "endset":
                            indent -= 4
                            local_scope_stack.pop()
                            var_name, capture_var = block_data
                            # The content was captured to capture_var list
                            # Join and assign to the variable
                            code.append(
                                f"{' ' * indent}{var_name} = ''.join({capture_var})"
                            )
                        elif block_type == "call" and stmt == "endcall":
                            # Handle call block ending
                            indent -= 4
                            local_scope_stack.pop()
                            # Call block data contains the macro name, args, and capture var
                            macro_name, args_part, capture_var, caller_var = block_data
                            # Join captured content and create caller function
                            code.append(
                                f"{' ' * indent}__caller_content = ''.join({capture_var})"
                            )
                            code.append(f"{' ' * indent}def {caller_var}():")
                            code.append(f"{' ' * (indent + 4)}return __caller_content")
                            # Call the macro with arguments and caller parameter
                            if args_part:
                                code.append(
                                    f"{' ' * indent}yield {macro_name}({args_part}, caller={caller_var})"
                                )
                            else:
                                code.append(
                                    f"{' ' * indent}yield {macro_name}(caller={caller_var})"
                                )
                        else:
                            # Regular block (if, for, with)
                            indent -= 4
                            local_scope_stack.pop()
                    else:
                        # Unbalanced end tag: log a warning or just ignore to prevent
                        # breaking function indentation (yield outside function error).
                        if is_debug:
                            code.append(
                                f"{' ' * indent}# Warning: Unbalanced {stmt} ignored"
                            )
            else:
                if token:
                    safe_token = repr(token)
                    # Check if we're capturing content for a set or call block
                    capture_var = _get_capture_var()
                    if capture_var:
                        code.append(
                            " " * indent + f"{capture_var}.append({safe_token})"
                        )
                    else:
                        code.append(" " * indent + f"yield {safe_token}")

        compiled_code = "\n".join(code)
        # SECURITY: Restrict the exec namespace. Setting __builtins__ to an
        # empty dict prevents compiled template code from accessing dangerous
        # Python builtins (import, eval, exec, open, __import__, etc.).
        # Safe builtins (range, len, str, etc.) are provided explicitly via
        # the _resolve_name() function during template execution.
        # Note: 'slice' is added to env because the AST parser translates
        # [x:y] into explicit slice() calls in the generated code.
        env = {
            "__builtins__": {},
            "_Loop": _Loop,
            "_escape": _escape,
            "slice": slice,
            "default_cache": default_cache,
        }
        try:
            exec(compiled_code, env)
        except Exception as e:
            raise TemplateError(
                f"Template Compilation Error: {str(e)}\n\nCode:\n{compiled_code}"
            )
        run_fn = env["__run_template"]
        _compiled_cache[cache_key] = run_fn

    return run_fn(
        context, TEMPLATE_FILTERS, TEMPLATE_TESTS, _get, _resolve_name, is_debug
    )
