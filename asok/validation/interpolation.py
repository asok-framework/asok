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

    Supports both generic {arg} and specific placeholders like {min}, {max}, {field}, etc.
    This allows for more natural, user-friendly error messages.

    Examples:
        "Minimum {min} characters" with rule "min:5" → "Minimum 5 characters"
        "Must be between {min} and {max}" with "between:10,20" → "Must be between 10 and 20"
        "{field} must match {other}" with field="password" → "Password must match password_confirm"
    """
    if not arg and not field:
        return text

    placeholders = {}

    # Always include generic {arg} for backward compatibility
    if arg:
        placeholders["arg"] = str(arg)

    # Add field name placeholders
    if field:
        placeholders["field"] = field.replace("_", " ").title()
        placeholders["name"] = field.replace("_", " ")

    # Add rule-specific placeholders
    if rule_name == "min" and arg:
        placeholders["min"] = str(arg)
    elif rule_name == "max" and arg:
        placeholders["max"] = str(arg)
    elif rule_name == "between" and arg:
        try:
            parts = str(arg).split(",")
            if len(parts) == 2:
                placeholders["min"] = parts[0].strip()
                placeholders["max"] = parts[1].strip()
                placeholders["between"] = str(arg)
        except Exception:
            pass
    elif rule_name == "ext" and arg:
        placeholders["extensions"] = str(arg)
        placeholders["ext"] = str(arg)
    elif rule_name == "size" and arg:
        placeholders["size"] = str(arg)
    elif rule_name == "same" and arg:
        placeholders["other"] = arg.replace("_", " ").title()
    elif rule_name == "in" and arg:
        placeholders["values"] = str(arg)
        placeholders["in"] = str(arg)

    # Replace all placeholders in the text
    for key, value in placeholders.items():
        text = text.replace(f"{{{key}}}", str(value))

    return text
