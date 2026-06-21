from __future__ import annotations

import builtins
from typing import Any, Union

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

# SECURITY: Dangerous attribute patterns that should never be accessible in templates.
# These can be used for sandbox escape via Python object introspection.
_DANGEROUS_ATTRS = frozenset(
    {
        "__class__",
        "__bases__",
        "__mro__",
        "__globals__",
        "__builtins__",
        "__code__",
        "__dict__",
        "__import__",
        "__loader__",
        "__spec__",
        "__package__",
        "__closure__",
        "__func__",
        "__self__",
        "func_globals",
        "func_code",
        "gi_code",
        "gi_frame",
        "cr_frame",
        "ag_frame",
    }
)


_BLOCKED_TEMPLATE_KEYS = frozenset({"eval", "exec", "compile", "open", "__import__"})
_DANGEROUS_CALL_KEYS = frozenset({"eval", "exec", "compile", "execfile"})


def _get(obj: Any, key: Union[str, int]) -> Any:
    """Access an attribute or dictionary/list key, favoring attributes for strings.

    Returns an empty string if the key/attribute is not found or the object is
    not subscriptable.
    """
    if isinstance(key, str):
        if _is_blocked_string_key(key):
            return ""
        result = _safe_getattr(obj, key)
        if result is not _ATTR_MISS:
            return result
    return _safe_subscript(obj, key)


_ATTR_MISS = object()


def _is_blocked_string_key(key: str) -> bool:
    # SECURITY: filter introspection attrs, dunders, and dangerous symbol names.
    if key in _DANGEROUS_ATTRS or key in _BLOCKED_TEMPLATE_KEYS:
        return True
    if _is_dunder(key):
        return True
    return _is_private_unsafe(key)


def _is_dunder(key: str) -> bool:
    return key.startswith("__") and key.endswith("__")


def _is_private_unsafe(key: str) -> bool:
    return key.startswith("_") and key not in _TEMPLATE_SAFE_ATTRS


def _safe_getattr(obj: Any, key: str) -> Any:
    try:
        result = getattr(obj, key)
    except (AttributeError, TypeError):
        return _ATTR_MISS
    # SECURITY: never expose callables matching well-known code-exec helpers.
    if callable(result) and key in _DANGEROUS_CALL_KEYS:
        return ""
    return result


def _safe_subscript(obj: Any, key: Union[str, int]) -> Any:
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return ""


_BLOCKED_BUILTINS = frozenset({
    "eval", "exec", "compile", "execfile", "__import__", "open",
    "type", "vars", "dir", "globals", "locals",
    "getattr", "setattr", "delattr", "hasattr", "__builtins__",
})

_ALLOWED_BUILTINS = frozenset({
    "range", "len", "dict", "str", "int", "float", "list", "enumerate",
    "bool", "abs", "min", "max", "sum", "sorted", "reversed",
})

_LITERAL_ALIASES = {"true": True, "false": False, "none": None}


def _resolve_name(context: dict[str, Any], name: str, is_debug: bool = False) -> Any:
    """Safely resolve a non-dotted name from context or builtins.

    SECURITY: only an explicit allow-list of builtins is exposed. User-defined
    variables in ``context`` still take precedence even if their name shadows a
    blocked builtin.
    """
    if name in context:
        return context[name]
    builtin_val = _resolve_builtin(name, is_debug)
    if builtin_val is not _NAME_MISS:
        return builtin_val
    # Match the production behaviour in dev too: an unknown name renders as
    # an empty string instead of raising. Without this, helpers like
    # ``providers|default([...])`` and ``providers is defined`` can't run
    # because the strict NameError fires before the filter/test executes —
    # making optional template parameters impossible in DEBUG=True.
    return ""


_NAME_MISS = object()


def _resolve_builtin(name: str, is_debug: bool) -> Any:
    if name in _BLOCKED_BUILTINS:
        return _blocked_builtin(name, is_debug)
    if name in _ALLOWED_BUILTINS:
        return getattr(builtins, name)
    if name in _LITERAL_ALIASES:
        return _LITERAL_ALIASES[name]
    return _NAME_MISS


def _blocked_builtin(name: str, is_debug: bool) -> str:
    if is_debug:
        raise NameError(
            f"Access to builtin '{name}' is forbidden in templates for security reasons."
        )
    return ""
