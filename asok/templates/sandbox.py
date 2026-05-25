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


def _get(obj: Any, key: Union[str, int]) -> Any:
    """Access an attribute or dictionary/list key, favoring attributes for strings.

    Returns an empty string if the key/attribute is not found or the object is not subscriptsable.
    """
    if isinstance(key, str):
        # SECURITY: Block access to dangerous attributes that enable sandbox escape
        if key in _DANGEROUS_ATTRS:
            return ""

        # SECURITY: Block access to dunder attributes entirely
        if key.startswith("__") and key.endswith("__"):
            return ""

        # SECURITY: Block access to potentially dangerous methods
        if key in ("eval", "exec", "compile", "open", "__import__"):
            return ""

        # SECURITY: Block single-underscore attributes unless whitelisted.
        # Return empty string for compatibility with existing templates.
        if key.startswith("_") and key not in _TEMPLATE_SAFE_ATTRS:
            return ""
        try:
            result = getattr(obj, key)
            # SECURITY: Block access to methods that could lead to code execution
            if callable(result) and key in ("eval", "exec", "compile", "execfile"):
                return ""
            return result
        except (AttributeError, TypeError):
            pass

    try:
        return obj[key]
    except (KeyError, TypeError, IndexError):
        return ""


def _resolve_name(context: dict[str, Any], name: str, is_debug: bool = False) -> Any:
    """Safely resolve a non-dotted name from context or builtins.

    SECURITY: Only explicitly whitelisted builtins are allowed.
    Dangerous functions (eval, exec, compile, __import__, open, type, etc.) are blocked
    when attempting to access them as builtins, but user-defined variables with these
    names in the context are allowed.
    """
    # Check context first - user-defined variables take precedence
    if name in context:
        return context[name]

    # SECURITY: Block dangerous builtin names explicitly
    # Only checked for builtins - not for user variables in context
    if name in (
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
    ):
        if is_debug:
            raise NameError(
                f"Access to builtin '{name}' is forbidden in templates for security reasons."
            )
        return ""

    # SECURITY: Explicitly allowed builtins only - minimal safe set for templates
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
        "abs",
        "min",
        "max",
        "sum",
        "sorted",
        "reversed",
    ):
        return getattr(builtins, name)

    # ALIASES for common lowercase constants (Jinja2-like)
    if name == "true":
        return True
    if name == "false":
        return False
    if name == "none":
        return None

    if is_debug:
        raise NameError(f"Variable '{name}' is not defined in template context.")

    return ""
