from __future__ import annotations

import builtins
from typing import Any, Union

from .undefined import Undefined

# Whitelist of single-underscore attributes that templates may legitimately access.
# Everything else starting with "_" is blocked to prevent sandbox escape
# (e.g. _get_conn, _table, _db_path on ORM models).
_TEMPLATE_SAFE_ATTRS = frozenset(
    {
        "_label",
        "_error",
        "_fields",
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


def _is_strict_variables(context: dict[str, Any]) -> bool:
    if context.get("STRICT_VARIABLES") or context.get("__strict_variables__"):
        return True
    return _is_app_strict_variables(context.get("request"))


def _is_app_strict_variables(req: Any) -> bool:
    if not req or not hasattr(req, "environ"):
        return False
    app = req.environ.get("asok.app")
    return bool(app and getattr(app, "config", {}).get("STRICT_VARIABLES"))


def _get(obj: Any, key: Union[str, int]) -> Any:
    """Access an attribute or dictionary/list key, favoring attributes for strings."""
    if isinstance(obj, Undefined):
        return _get_undefined_key(obj, key)
    if isinstance(key, str):
        return _get_str_key(obj, key)
    return _safe_subscript(obj, key)


def _get_undefined_key(obj: Undefined, key: Union[str, int]) -> Any:
    return getattr(obj, str(key)) if isinstance(key, str) else obj[key]


def _get_str_key(obj: Any, key: str) -> Any:
    if _is_blocked_string_key(key):
        return ""
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    result = _safe_getattr(obj, key)
    return result if result is not _ATTR_MISS else _safe_subscript(obj, key)


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
    if callable(result) and key in _DANGEROUS_CALL_KEYS:
        return ""
    return result


def _safe_subscript(obj: Any, key: Union[str, int]) -> Any:
    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return Undefined(str(key))


_BLOCKED_BUILTINS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "execfile",
        "__import__",
        "open",
        "type",
        "vars",
        "dir",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "__builtins__",
    }
)

_ALLOWED_BUILTINS = frozenset(
    {
        "range",
        "len",
        "dict",
        "str",
        "int",
        "float",
        "list",
        "enumerate",
        "bool",
        "abs",
        "min",
        "max",
        "sum",
        "sorted",
        "reversed",
    }
)

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
    strict = _is_strict_variables(context)
    return Undefined(name, strict=strict)


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
