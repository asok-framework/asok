"""Per-statement compilers for ``{% ... %}`` tokens.

Each ``_compile_*`` mutates the ``CompilerState``. The shared dispatch helper
in ``_compile_and_run`` routes a stripped statement to the right handler.
Keeping each handler small makes them all stay at A complexity.
"""

from __future__ import annotations

from ._compiler_state import CompilerState

END_STATEMENTS = {
    "endif",
    "endfor",
    "endset",
    "endwith",
    "endcall",
    "endcache",
}


def compile_set(state: CompilerState, stmt: str) -> None:
    rest = stmt[4:].strip()
    if "=" in rest:
        _compile_set_inline(state, rest)
    else:
        _compile_set_block(state, rest)


def _compile_set_inline(state: CompilerState, rest: str) -> None:
    var_name, expr = rest.split("=", 1)
    var_name = var_name.strip()
    state.local_scope_stack[-1].add(var_name)
    state.emit(f"{var_name} = {state.resolve_full(expr.strip())}")


def _compile_set_block(state: CompilerState, rest: str) -> None:
    var_name = rest.strip()
    state.local_scope_stack[-1].add(var_name)
    capture_var = state.gen_token("__set_capture")
    state.emit(f"{capture_var} = []")
    state.emit("if True:  # set block scope")
    state.open_block("set", (var_name, capture_var), scope={capture_var})


def compile_do(state: CompilerState, stmt: str) -> None:
    state.emit(state.resolve_full(stmt[3:].strip()))


def compile_break(state: CompilerState, stmt: str) -> None:
    state.emit("break")


def compile_continue(state: CompilerState, stmt: str) -> None:
    state.emit("continue")


def compile_cache(state: CompilerState, stmt: str) -> None:
    parts = stmt[6:].strip().split(maxsplit=1)
    key_expr = parts[0]
    ttl_expr = parts[1] if len(parts) > 1 else "None"
    key_resolved = state.resolve_full(key_expr)
    ttl_resolved = state.resolve_full(ttl_expr)
    cache_id = state.gen_token("").lstrip("_")
    cache_capture_var = f"__cache_capture_{cache_id}"
    state.emit(f"__cache_val_{cache_id} = default_cache.get({key_resolved})")
    state.emit(f"if __cache_val_{cache_id} is not None:")
    state.emit(f"    yield __cache_val_{cache_id}")
    state.emit("else:")
    state.open_block(
        "cache",
        (cache_capture_var, key_resolved, ttl_resolved, cache_id),
        scope={cache_capture_var},
    )
    state.emit(f"{cache_capture_var} = []")


def compile_call(state: CompilerState, stmt: str) -> None:
    macro_call = stmt[5:].strip()
    macro_name, args_part = _split_macro_call(macro_call)
    resolved_name = state.resolve_full(macro_name)
    capture_var = state.gen_token("__capture")
    caller_var = state.gen_token("__caller")
    state.emit(f"{capture_var} = []")
    state.emit("if True:  # call block scope")
    state.open_block(
        "call",
        (resolved_name, args_part, capture_var, caller_var),
        scope={capture_var, caller_var},
    )


def _split_macro_call(macro_call: str) -> tuple[str, str]:
    if "(" not in macro_call:
        return macro_call, ""
    name, args = macro_call.split("(", 1)
    return name.strip(), args.rstrip(")")


def compile_with(state: CompilerState, stmt: str) -> None:
    var_part = stmt[5:].strip()
    if "=" not in var_part:
        return
    new_scope: set[str] = set()
    for assignment in (a.strip() for a in var_part.split(",")):
        _emit_with_assignment(state, assignment, new_scope)
    state.emit("if True:")
    state.open_block("with", None, scope=new_scope)


def _emit_with_assignment(
    state: CompilerState, assignment: str, new_scope: set[str]
) -> None:
    if "=" not in assignment:
        return
    var_name, expr = assignment.split("=", 1)
    var_name = var_name.strip()
    new_scope.add(var_name)
    state.emit(f"{var_name} = {state.resolve_full(expr.strip())}")


def compile_for(state: CompilerState, stmt: str) -> None:
    try:
        loop_vars_part, collection = stmt[4:].split(" in ", 1)
    except ValueError:
        state.emit("for _ in []:")
        state.open_block("for", None, scope=set())
        return
    loop_id = state.gen_token("").lstrip("_")
    coll_resolved = state.resolve(collection.strip())
    state.emit(f"__loop_{loop_id} = _Loop({coll_resolved})")
    state.emit(f"for {loop_vars_part.strip()} in __loop_{loop_id}:")
    scope = {"loop"} | {v.strip() for v in loop_vars_part.split(",") if v.strip()}
    state.open_block("for", loop_id, scope=scope)
    state.emit(f"loop = __loop_{loop_id}")


def compile_if(state: CompilerState, stmt: str) -> None:
    state.emit("if " + state.resolve(stmt[3:].strip()) + ":")
    state.open_block("if", None, scope=set())


def compile_elif(state: CompilerState, stmt: str) -> None:
    _pop_branch_for_elif_else(state)
    state.emit("elif " + state.resolve(stmt[5:].strip()) + ":")
    state.indent += 4


def compile_else(state: CompilerState, stmt: str) -> None:
    _pop_branch_for_elif_else(state)
    head = state.block_stack[-1] if state.block_stack else (None, None)
    if head[0] == "for":
        loop_id = head[1]
        state.emit(f"if not __loop_{loop_id}.length:" if loop_id else "else:")
    else:
        state.emit("else:")
    state.indent += 4


def _pop_branch_for_elif_else(state: CompilerState) -> None:
    if not state.block_stack or state.block_stack[-1][0] not in ("if", "for"):
        return
    state.indent -= 4
    state.local_scope_stack.pop()
    state.local_scope_stack.append(set())


def compile_end(state: CompilerState, stmt: str) -> None:
    block = state.close_block()
    if block is None:
        if state.is_debug:
            state.emit(f"# Warning: Unbalanced {stmt} ignored")
        return
    block_type, block_data = block
    handler = _END_HANDLERS.get((block_type, stmt))
    if handler:
        handler(state, block_data)


def _close_cache(state: CompilerState, block_data) -> None:
    capture_var, key_resolved, ttl_resolved, cache_id = block_data
    state.emit(f"__cache_output_{cache_id} = ''.join({capture_var})")
    state.emit(
        f"default_cache.set({key_resolved}, __cache_output_{cache_id}, "
        f"ttl={ttl_resolved})"
    )
    state.emit(f"yield __cache_output_{cache_id}")


def _close_set(state: CompilerState, block_data) -> None:
    var_name, capture_var = block_data
    state.emit(f"{var_name} = ''.join({capture_var})")


def _close_call(state: CompilerState, block_data) -> None:
    macro_name, args_part, capture_var, caller_var = block_data
    state.emit(f"__caller_content = ''.join({capture_var})")
    state.emit(f"def {caller_var}():")
    state.emit("    return __caller_content")
    if args_part:
        state.emit(f"yield {macro_name}({args_part}, caller={caller_var})")
    else:
        state.emit(f"yield {macro_name}(caller={caller_var})")


_END_HANDLERS = {
    ("cache", "endcache"): _close_cache,
    ("set", "endset"): _close_set,
    ("call", "endcall"): _close_call,
}


_BRANCH_PREFIXES = ("for ", "if ", "elif ", "else")
