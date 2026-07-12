from __future__ import annotations

import hashlib
from typing import Any, Callable, Optional

from asok.cache import default_cache
from asok.exceptions import TemplateError

from ._compiler_state import CompilerState
from ._statement_compiler import (
    END_STATEMENTS,
    compile_break,
    compile_cache,
    compile_call,
    compile_continue,
    compile_do,
    compile_elif,
    compile_else,
    compile_end,
    compile_for,
    compile_if,
    compile_set,
    compile_with,
)
from .filters import TEMPLATE_FILTERS
from .loop import _Loop
from .preprocessor import _RE_TOKENS, _macro_cache, _macro_mtimes
from .safestring import _escape
from .sandbox import _get, _resolve_name
from .tests import TEMPLATE_TESTS

# Caches
_compiled_cache: dict[str, Callable[..., Any]] = {}
_dotted_cache: dict[str, Any] = {}


def clear_template_caches() -> None:
    """Clear all template caches. Useful in development mode."""
    _compiled_cache.clear()
    _dotted_cache.clear()
    _macro_cache.clear()
    _macro_mtimes.clear()


_STATEMENT_PREFIX_HANDLERS_DICT = {
    "set": compile_set,
    "do": compile_do,
    "cache": compile_cache,
    "call": compile_call,
    "with": compile_with,
    "for": compile_for,
    "if": compile_if,
    "elif": compile_elif,
}

_STATEMENT_EXACT_HANDLERS = {
    "break": compile_break,
    "continue": compile_continue,
}


def _strip_token_padding(token: str) -> str:
    inner = token[2:-2].strip().lstrip("-").rstrip("-").strip()
    return " ".join(inner.split())


def _emit_yield_or_capture(state: CompilerState, text: str) -> None:
    capture_var = state.capture_var()
    if capture_var:
        state.emit(f"{capture_var}.append({text})")
    else:
        state.emit(f"yield {text}")


def _handle_expression_token(state: CompilerState, token: str) -> None:
    expr = _strip_token_padding(token)
    _emit_yield_or_capture(state, f"_escape({state.resolve_full(expr)})")


def _handle_statement_token(state: CompilerState, token: str) -> None:
    stmt = _strip_token_padding(token)
    if stmt in _STATEMENT_EXACT_HANDLERS:
        _STATEMENT_EXACT_HANDLERS[stmt](state, stmt)
        return
    if stmt.startswith("else"):
        compile_else(state, stmt)
        return
    if stmt in END_STATEMENTS:
        compile_end(state, stmt)
        return
    _dispatch_statement_prefix(state, stmt)


def _dispatch_statement_prefix(state: CompilerState, stmt: str) -> None:
    parts = stmt.split(None, 1)
    if parts:
        handler = _STATEMENT_PREFIX_HANDLERS_DICT.get(parts[0])
        if handler is not None:
            handler(state, stmt)


def _handle_literal_token(state: CompilerState, token: str) -> None:
    if not token:
        return
    _emit_yield_or_capture(state, repr(token))


def _emit_for_token(state: CompilerState, token: str) -> None:
    if token.startswith("{{"):
        _handle_expression_token(state, token)
    elif token.startswith("{%"):
        _handle_statement_token(state, token)
    else:
        _handle_literal_token(state, token)


def _check_unclosed_delimiters(
    token: str, current_line: int, template_name: Optional[str]
) -> None:
    if "{{" in token:
        idx = token.find("{{")
        line_offset = token[:idx].count("\n")
        prefix = f"Template '{template_name}'" if template_name else "Template"
        raise TemplateError(
            f"{prefix} Syntax Error: Unclosed template expression '{{' on line {current_line + line_offset}"
        )
    if "{%" in token:
        idx = token.find("{%")
        line_offset = token[:idx].count("\n")
        prefix = f"Template '{template_name}'" if template_name else "Template"
        raise TemplateError(
            f"{prefix} Syntax Error: Unclosed template statement '{{%' on line {current_line + line_offset}"
        )


def _build_compiled_callable(
    template_string: str, is_debug: bool, template_name: Optional[str] = None
) -> Callable[..., Any]:
    state = CompilerState(is_debug)
    current_line = 1
    for token in _RE_TOKENS.split(template_string):
        if token.startswith("{{") or token.startswith("{%"):
            state.emit(f"__template_line__ = {current_line}")
            _emit_for_token(state, token)
        else:
            _check_unclosed_delimiters(token, current_line, template_name)
            _emit_for_token(state, token)
        current_line += token.count("\n")
    compiled_code = "\n".join(state.code)
    run_fn = _exec_template_code(compiled_code, template_name)
    run_fn._compiled_code = compiled_code
    return run_fn


def _exec_template_code(
    compiled_code: str, template_name: Optional[str] = None
) -> Callable[..., Any]:
    # SECURITY: empty __builtins__ blocks ``exec``/``eval``/``open``/``__import__``
    # from compiled templates. Safe builtins (range, len, str, ...) are resolved
    # explicitly through _resolve_name() at run time.
    env = {
        "__builtins__": {},
        "_Loop": _Loop,
        "_escape": _escape,
        "slice": slice,
        "str": str,
        "int": int,
        "float": float,
        "len": len,
        "range": range,
        "dict": dict,
        "list": list,
        "bool": bool,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "default_cache": default_cache,
    }
    try:
        exec(compiled_code, env)
    except Exception as e:
        prefix = f"Template '{template_name}'" if template_name else "Template"
        raise TemplateError(
            f"{prefix} Compilation Error: {str(e)}\n\nCode:\n{compiled_code}"
        )
    return env["__run_template"]


def _compile_and_run(
    template_string: str,
    context: dict[str, Any],
    is_debug: bool = False,
    cache_key: Optional[str] = None,
    template_name: Optional[str] = None,
) -> Any:
    """Compile a pre-processed template string and execute it."""
    if cache_key is None:
        cache_key = hashlib.md5(template_string.encode()).hexdigest()
    if is_debug:
        cache_key += "_debug"
    run_fn = _compiled_cache.get(cache_key)
    if run_fn is None:
        run_fn = _build_compiled_callable(template_string, is_debug, template_name)
        _compiled_cache[cache_key] = run_fn
    gen = run_fn(
        context, TEMPLATE_FILTERS, TEMPLATE_TESTS, _get, _resolve_name, is_debug
    )

    def _generator_wrapper() -> Any:
        try:
            yield from gen
        except Exception as e:
            import sys

            tb = sys.exc_info()[2]
            line_no = None
            while tb:
                frame = tb.tb_frame
                if "__template_line__" in frame.f_locals:
                    line_no = frame.f_locals["__template_line__"]
                tb = tb.tb_next

            code_str = getattr(run_fn, "_compiled_code", "unknown")
            msg = str(e)
            prefix = ""
            if template_name:
                prefix = f"Error rendering template '{template_name}'"
                if line_no is not None:
                    prefix += f" on line {line_no}"
            elif line_no is not None:
                prefix = f"Error rendering template on line {line_no}"

            if prefix:
                msg = f"{prefix}: {msg}"

            raise TemplateError(
                f"{msg}\n\nCompiled Code:\n{code_str}\n\nContext keys: {list(context.keys())}"
            ) from e

    return _generator_wrapper()
