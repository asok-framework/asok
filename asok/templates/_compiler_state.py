"""Compiler state for ``_compile_and_run``.

Pulls the giant elif chain out of one function and into a small object that
tracks the indentation, the lexical scope, and the currently-open template
blocks. Each token handler stays at A complexity.
"""

from __future__ import annotations

import secrets
from typing import Any, Optional

from .resolver import _resolve_expr, _resolve_expr_full

_INITIAL_SCOPE = frozenset(
    {"context", "__filters", "__tests", "_get", "_res", "_debug"}
)


class CompilerState:
    def __init__(self, is_debug: bool) -> None:
        self.is_debug = is_debug
        self.code: list[str] = [
            "def __run_template(context, __filters, __tests, _get, _res, _debug):",
            "    pass",
        ]
        self.indent = 4
        self.local_scope_stack: list[set[str]] = [set(_INITIAL_SCOPE)]
        self.block_stack: list[tuple[str, Any]] = []

    # ── scope helpers ────────────────────────────────────────────

    def all_locals(self) -> set[str]:
        s: set[str] = set()
        for level in self.local_scope_stack:
            s.update(level)
        return s

    def capture_var(self) -> Optional[str]:
        for block_type, block_data in reversed(self.block_stack):
            if block_type == "set":
                return block_data[1]
            if block_type == "call":
                return block_data[2]
            if block_type == "cache":
                return block_data[0]
        return None

    # ── code emission ───────────────────────────────────────────

    def emit(self, line: str) -> None:
        self.code.append(" " * self.indent + line)

    def emit_raw(self, line: str) -> None:
        self.code.append(line)

    def open_block(self, kind: str, data: Any, scope: set[str] | None = None) -> None:
        self.block_stack.append((kind, data))
        self.indent += 4
        self.local_scope_stack.append(scope or set())

    def close_block(self) -> tuple[str, Any] | None:
        if not self.block_stack:
            return None
        self.indent -= 4
        self.local_scope_stack.pop()
        return self.block_stack.pop()

    # ── expression resolution helpers ───────────────────────────

    def resolve_full(self, expr: str) -> str:
        return _resolve_expr_full(expr, self.all_locals(), self.is_debug)

    def resolve(self, expr: str) -> str:
        return _resolve_expr(expr, self.all_locals(), self.is_debug)

    # ── name generators ────────────────────────────────────────

    @staticmethod
    def gen_token(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(4)}"
