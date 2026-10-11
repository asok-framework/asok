"""Validator rule handlers.

Each handler reads the relevant inputs from the Validator state, calls a
``check_*`` from ``rules``, and writes the error message via the supplied
``Validator._msg`` helper. Pulling these out of the giant ``Validator.rule``
elif chain keeps each handler at A complexity.
"""

from __future__ import annotations

import datetime
import json as json_mod
from typing import Any, Callable

from . import rules as rules_mod


def _simple_handler(check_name: str, default_arg: Any = None):
    check_fn = getattr(rules_mod, f"check_{check_name}")

    def handler(validator, field: str, arg, messages: dict) -> None:
        val = validator.data.get(field, default_arg)
        if not check_fn(val):
            validator.errors[field] = validator._msg(check_name, messages, field=field)

    return handler


def _file_handler(check_name: str):
    check_fn = getattr(rules_mod, f"check_{check_name}")

    def handler(validator, field: str, arg, messages: dict) -> None:
        f = validator.files.get(field)
        if not check_fn(f):
            validator.errors[field] = validator._msg(check_name, messages, field=field)

    return handler


def _arg_handler(check_name: str):
    check_fn = getattr(rules_mod, f"check_{check_name}")

    def handler(validator, field: str, arg, messages: dict) -> None:
        if not arg:
            raise ValueError(
                f"Rule '{check_name}' requires an argument (e.g. '{check_name}:<value>')"
            )
        val = validator.data.get(field, "")
        if not check_fn(val, arg):
            validator.errors[field] = validator._msg(check_name, messages, arg, field)

    return handler


def _file_arg_handler(check_name: str):
    check_fn = getattr(rules_mod, f"check_{check_name}")

    def handler(validator, field: str, arg, messages: dict) -> None:
        if not arg:
            raise ValueError(
                f"Rule '{check_name}' requires an argument (e.g. '{check_name}:<value>')"
            )
        f = validator.files.get(field)
        if not check_fn(f, arg):
            validator.errors[field] = validator._msg(check_name, messages, arg, field)

    return handler


def _handle_required(validator, field: str, arg, messages: dict) -> None:
    val = validator.data.get(field)
    f = validator.files.get(field)
    if not rules_mod.check_required(val, f):
        validator.errors[field] = validator._msg("required", messages, field=field)


def _handle_unique(validator, field: str, arg, messages: dict) -> None:
    if not arg or "," not in arg:
        raise ValueError(
            "Rule 'unique' requires an argument: 'unique:<model>,<column>'"
        )
    model_name, field_name = arg.split(",", 1)
    if not rules_mod.check_unique(validator.data.get(field), model_name, field_name):
        validator.errors[field] = validator._msg("unique", messages, field=field)


def _handle_confirmed(validator, field: str, arg, messages: dict) -> None:
    val = validator.data.get(field, "")
    confirm_val = validator.data.get(f"{field}_confirmation", "")
    if not rules_mod.check_confirmed(val, confirm_val):
        validator.errors[field] = validator._msg("confirmed", messages, field=field)


def _handle_required_if(validator, field: str, arg, messages: dict) -> None:
    if not arg or "," not in arg:
        raise ValueError(
            "Rule 'required_if' requires an argument: 'required_if:<field>,<expected>'"
        )
    other, expected = arg.split(",", 1)
    val = validator.data.get(field)
    other_val = validator.data.get(other, "")
    if not rules_mod.check_required_if(val, other_val, expected):
        validator.errors[field] = validator._msg("required_if", messages, field=field)


def _handle_required_with(validator, field: str, arg, messages: dict) -> None:
    if not arg:
        raise ValueError(
            "Rule 'required_with' requires a target field argument: 'required_with:<field>'"
        )
    val = validator.data.get(field)
    other_val = validator.data.get(arg)
    if not rules_mod.check_required_with(val, other_val):
        validator.errors[field] = validator._msg("required_with", messages, field=field)


def _handle_between(validator, field: str, arg, messages: dict) -> None:
    if not arg or "," not in arg:
        raise ValueError("Rule 'between' requires two bounds: 'between:<min>,<max>'")
    lo, hi = arg.split(",", 1)
    val = validator.data.get(field, "")
    if not rules_mod.check_between(val, lo, hi):
        validator.errors[field] = validator._msg("between", messages, arg, field)


def _handle_same(validator, field: str, arg, messages: dict) -> None:
    if not arg:
        raise ValueError("Rule 'same' requires a target field argument: 'same:<field>'")
    val = validator.data.get(field, "")
    other_val = validator.data.get(arg, "")
    if not rules_mod.check_same(val, other_val):
        validator.errors[field] = validator._msg("same", messages, arg, field)


def _decode_daterange_val(val):
    """Decode a daterange value from JSON. Returns None when too large."""
    if isinstance(val, str):
        if len(val) > 10_000:
            return None
        return json_mod.loads(val)
    return val


def _handle_daterange(validator, field: str, arg, messages: dict) -> None:
    val = validator.data.get(field)
    if not val:
        return
    try:
        d = _decode_daterange_val(val)
        if d is None:
            raise ValueError("daterange payload too large")
        start = d.get("start")
        end = d.get("end")
        _check_daterange_payload(validator, field, arg, messages, start, end)
    except (
        ValueError,
        json_mod.JSONDecodeError,
        TypeError,
        AttributeError,
        RecursionError,
    ):
        validator.errors[field] = validator._msg(
            "daterange_invalid", messages, field=field
        )


def _check_daterange_payload(validator, field, arg, messages, start, end) -> None:
    if not (start and end):
        return
    ds = datetime.date.fromisoformat(start)
    de = datetime.date.fromisoformat(end)
    _record_daterange_errors(validator, field, arg, messages, ds, de)


def _record_daterange_errors(validator, field, arg, messages, ds, de) -> None:
    if de < ds:
        validator.errors[field] = validator._msg(
            "daterange_order", messages, field=field
        )
    if arg == "future" and ds < datetime.date.today():
        validator.errors[field] = validator._msg(
            "daterange_future", messages, field=field
        )


_HANDLERS: dict[str, Callable] = {
    "required": _handle_required,
    "email": _simple_handler("email", default_arg=None),
    "min": _arg_handler("min"),
    "max": _arg_handler("max"),
    "unique": _handle_unique,
    "ext": _file_arg_handler("ext"),
    "size": _file_arg_handler("size"),
    "confirmed": _handle_confirmed,
    "in": _arg_handler("in"),
    "numeric": _simple_handler("numeric", default_arg=""),
    "regex": _arg_handler("regex"),
    "url": _simple_handler("url", default_arg=""),
    "date": _simple_handler("date", default_arg=""),
    "required_if": _handle_required_if,
    "required_with": _handle_required_with,
    "between": _handle_between,
    "digits": _arg_handler("digits"),
    "boolean": _simple_handler("boolean", default_arg=None),
    "slug": _simple_handler("slug", default_arg=""),
    "uuid": _simple_handler("uuid", default_arg=""),
    "alpha": _simple_handler("alpha", default_arg=""),
    "alpha_num": _simple_handler("alpha_num", default_arg=""),
    "alphanumeric": _simple_handler("alpha_num", default_arg=""),
    "integer": _simple_handler("integer", default_arg=""),
    "tel": _simple_handler("tel", default_arg=""),
    "image": _file_handler("image"),
    "password_strength": _simple_handler("password_strength", default_arg=""),
    "color": _simple_handler("color", default_arg=""),
    "month": _simple_handler("month", default_arg=""),
    "base64": _simple_handler("base64", default_arg=""),
    "same": _handle_same,
    "json": _simple_handler("json", default_arg=""),
    "daterange": _handle_daterange,
}


def dispatch_rule(validator, field: str, name: str, arg, messages: dict) -> bool:
    """Apply one rule. Returns True if dispatched, False if unknown to the registry."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return False
    handler(validator, field, arg, messages)
    return True
