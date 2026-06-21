from __future__ import annotations

from typing import Any, Optional

_DEFAULT_MESSAGES = {
    "required": "This field is required.",
    "email": "Invalid email address.",
    "min": "Minimum {min} characters.",
    "max": "Maximum {max} characters.",
    "unique": "This value is already taken.",
    "ext": "Allowed extensions: {extensions}.",
    "size": "File too large (max {size}).",
    "confirmed": "Confirmation does not match.",
    "in": "Must be one of: {values}.",
    "numeric": "Must be a numeric value.",
    "digits": "Must be exactly {digits} digits.",
    "regex": "Invalid format.",
    "url": "Invalid URL.",
    "date": "Invalid date format.",
    "boolean": "Must be a boolean value.",
    "slug": "Invalid slug format.",
    "uuid": "Invalid UUID format.",
    "same": "Must match {field}.",
    "between": "Must be between {min} and {max}.",
    "alpha": "Only letters allowed.",
    "alpha_num": "Only letters and numbers allowed.",
    "required_if": "This field is required.",
    "required_with": "This field is required.",
    "json": "Invalid JSON format.",
    "tel": "Invalid telephone number.",
    "color": "Invalid color format (use #RRGGBB).",
    "month": "Invalid month format (use YYYY-MM).",
    "image": "The file must be a valid image (JPEG, PNG, GIF, WEBP).",
    "password_strength": "Password must be 8+ chars, with uppercase, number, and special char.",
    "base64": "Invalid base64 encoded data.",
    "daterange_order": "End date cannot be before start date.",
    "daterange_future": "Date range must start in the future.",
    "daterange_invalid": "Invalid date range format.",
}


def _interpolate(
    text: str, field: Optional[str], rule_name: str, arg: Optional[Any]
) -> str:
    """Interpolate placeholders in error messages with contextual values.

    Supports {arg} and specific placeholders like {min}, {max}, {field}, etc.

    Examples:
        "Minimum {min} characters" with rule "min:5" → "Minimum 5 characters"
        "{field} must match {other}" with field="password" → "Password must match password_confirm"
    """
    if not arg and not field:
        return text
    placeholders = _build_placeholders(field, arg)
    _RULE_PLACEHOLDERS.get(rule_name, lambda *_: None)(arg, placeholders)
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", str(value))
    return text


def _build_placeholders(field, arg) -> dict[str, str]:
    placeholders: dict[str, str] = {}
    if arg:
        placeholders["arg"] = str(arg)
    if field:
        placeholders["field"] = field.replace("_", " ").title()
        placeholders["name"] = field.replace("_", " ")
    return placeholders


def _rule_min(arg, p):
    if arg:
        p["min"] = str(arg)


def _rule_max(arg, p):
    if arg:
        p["max"] = str(arg)


def _rule_between(arg, p):
    if not arg:
        return
    try:
        parts = str(arg).split(",")
    except Exception:
        return
    if len(parts) == 2:
        p["min"] = parts[0].strip()
        p["max"] = parts[1].strip()
        p["between"] = str(arg)


def _rule_ext(arg, p):
    if arg:
        p["extensions"] = str(arg)
        p["ext"] = str(arg)


def _rule_size(arg, p):
    if arg:
        p["size"] = str(arg)


def _rule_same(arg, p):
    if arg:
        p["other"] = arg.replace("_", " ").title()


def _rule_in(arg, p):
    if arg:
        p["values"] = str(arg)
        p["in"] = str(arg)


def _rule_digits(arg, p):
    if arg:
        p["digits"] = str(arg)


_RULE_PLACEHOLDERS = {
    "min": _rule_min,
    "max": _rule_max,
    "between": _rule_between,
    "ext": _rule_ext,
    "size": _rule_size,
    "same": _rule_same,
    "in": _rule_in,
    "digits": _rule_digits,
}
