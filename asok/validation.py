from __future__ import annotations

import datetime
import json as json_mod
import re
from typing import Any, Callable, Optional, Union

from .orm import MODELS_REGISTRY, Field

_RE_EMAIL = re.compile(r"[^@]+@[^@]+\.[^@]+")
_RE_URL = re.compile(r"https?://[^\s/$.?#].[^\s]*")
_RE_TEL = re.compile(r"^\+?[0-9\s\-()\.]{7,20}$")
_regex_cache = {}

# SECURITY: Maximum input length for regex validation to prevent ReDoS attacks
# Inputs longer than this will be rejected before regex matching
_MAX_REGEX_INPUT_LENGTH = 10000

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
}


_CUSTOM_RULES = {}


def _interpolate(text: str, field: Optional[str], rule_name: str, arg: Optional[Any]) -> str:
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
        placeholders['arg'] = str(arg)

    # Add field name placeholders
    if field:
        placeholders['field'] = field.replace('_', ' ').title()
        placeholders['name'] = field.replace('_', ' ')

    # Add rule-specific placeholders
    if rule_name == 'min' and arg:
        placeholders['min'] = str(arg)
    elif rule_name == 'max' and arg:
        placeholders['max'] = str(arg)
    elif rule_name == 'between' and arg:
        try:
            parts = str(arg).split(',')
            if len(parts) == 2:
                placeholders['min'] = parts[0].strip()
                placeholders['max'] = parts[1].strip()
                placeholders['between'] = str(arg)
        except Exception:
            pass
    elif rule_name == 'ext' and arg:
        placeholders['extensions'] = str(arg)
        placeholders['ext'] = str(arg)
    elif rule_name == 'size' and arg:
        placeholders['size'] = str(arg)
    elif rule_name == 'same' and arg:
        placeholders['other'] = arg.replace('_', ' ').title()
    elif rule_name == 'in' and arg:
        placeholders['values'] = str(arg)
        placeholders['in'] = str(arg)

    # Replace all placeholders in the text
    for key, value in placeholders.items():
        text = text.replace(f'{{{key}}}', str(value))

    return text


def register_rule(
    name: str,
    fn: Callable[[Any, Optional[str], dict[str, Any]], bool],
    message: str = "Invalid value.",
) -> None:
    """Register a global custom validation rule.

    Args:
        name: The rule name (e.g., 'even').
        fn: A callable taking (value, argument, full_data) and returning a boolean.
        message: The default error message for this rule.
    """
    _CUSTOM_RULES[name] = (fn, message)


class Validator:
    """Engine for validating dictionaries and uploaded files against a set of rules."""

    def __init__(
        self,
        data: dict[str, Any],
        files: Optional[dict[str, Any]] = None,
        translate: Optional[Callable[[str], str]] = None,
    ):
        """Initialize the validator with data to check."""
        self.data = data
        self.files = files or {}
        self.errors: dict[str, str] = {}
        self._t = translate

    def _msg(
        self, rule_name: str, messages: dict[str, str], arg: Optional[Any] = None, field: Optional[str] = None
    ) -> str:
        """Resolve and format the error message for a specific rule failure."""
        if rule_name in messages:
            text = messages[rule_name]
        else:
            default = _DEFAULT_MESSAGES.get(rule_name, "Invalid value.")
            text = self._t(f"v_{rule_name}") if self._t else default
            # If translation key not found, _t returns the key itself — fall back to default
            if text == f"v_{rule_name}":
                text = default
        # Interpolate placeholders with contextual values
        text = _interpolate(text, field, rule_name, arg)
        return text

    def rule(
        self, field: str, rules: str, messages: Optional[dict[str, str]] = None
    ) -> bool:
        """Apply a set of rules (piped string) to a single field."""
        messages = messages or {}

        if "|" in rules:
            rule_list = rules.split("|")
        else:
            rule_list = [rules]

        for r in rule_list:
            parts = r.split(":")
            name = parts[0]
            arg = parts[1] if len(parts) > 1 else None

            # 1. Required
            if name == "required":
                val = self.data.get(field)
                if val is None or str(val).strip() == "":
                    f = self.files.get(field)
                    if not f or not getattr(f, "content", None):
                        self.errors[field] = self._msg("required", messages, field=field)

            # 2. Email
            if name == "email":
                val = self.data.get(field)
                # SECURITY: Limit input length to prevent ReDoS attacks
                if val:
                    val_str = str(val)
                    if len(val_str) > _MAX_REGEX_INPUT_LENGTH or not _RE_EMAIL.match(val_str):
                        self.errors[field] = self._msg("email", messages, field=field)

            # 3. Min Length
            if name == "min" and arg:
                val = self.data.get(field, "")
                if len(str(val)) < int(arg):
                    self.errors[field] = self._msg("min", messages, arg, field)

            # 4. Max Length
            if name == "max" and arg:
                val = self.data.get(field, "")
                if len(str(val)) > int(arg):
                    self.errors[field] = self._msg("max", messages, arg, field)

            # 5. Unique (format unique:Model,field)
            if name == "unique" and arg:
                model_name, field_name = arg.split(",")
                model = MODELS_REGISTRY.get(model_name)
                if model:
                    val = self.data.get(field)
                    if model.find(**{field_name: val}):
                        self.errors[field] = self._msg("unique", messages, field=field)

            # 6. File Extensions (ext:jpg,png)
            if name == "ext" and arg:
                f = self.files.get(field)
                if f:
                    exts = arg.split(",")
                    filename = getattr(f, "filename", "").lower()
                    if not any(filename.endswith("." + e) for e in exts):
                        self.errors[field] = self._msg("ext", messages, arg, field)

            # 7. File Size (size:2M)
            if name == "size" and arg:
                f = self.files.get(field)
                if f:
                    limit = arg.lower()
                    units = {"k": 1024, "m": 1024**2, "g": 1024**3}
                    try:
                        num = float(limit[:-1]) if limit[-1] in units else float(limit)
                        bytes_limit = num * units.get(limit[-1], 1)
                        if len(getattr(f, "content", b"")) > bytes_limit:
                            self.errors[field] = self._msg("size", messages, arg, field)
                    except ValueError:
                        pass

            # 8. Confirmed
            if name == "confirmed":
                val = self.data.get(field, "")
                confirm_val = self.data.get(f"{field}_confirmation", "")
                if val != confirm_val:
                    self.errors[field] = self._msg("confirmed", messages, field=field)

            # 9. In list (in:a,b,c)
            if name == "in" and arg:
                val = str(self.data.get(field, ""))
                # Allow empty values for nullable fields (skip validation if empty)
                if val:  # Only validate if value is not empty
                    allowed = arg.split(",")
                    if val not in allowed:
                        self.errors[field] = self._msg("in", messages, arg, field)

            # 10. Numeric
            if name == "numeric":
                val = self.data.get(field, "")
                if (
                    val
                    and not str(val).replace(".", "", 1).replace("-", "", 1).isdigit()
                ):
                    self.errors[field] = self._msg("numeric", messages, field=field)

            # 11. Regex (regex:pattern)
            if name == "regex" and arg:
                val = str(self.data.get(field, ""))
                if val:
                    # SECURITY: Limit input length to prevent ReDoS attacks
                    if len(val) > _MAX_REGEX_INPUT_LENGTH:
                        self.errors[field] = self._msg("regex", messages, field=field)
                        continue

                    pattern = _regex_cache.get(arg)
                    if pattern is None:
                        try:
                            pattern = re.compile(arg)
                            _regex_cache[arg] = pattern
                        except re.error:
                            self.errors[field] = self._msg("regex", messages, field=field)
                            continue
                    if not pattern.match(val):
                        self.errors[field] = self._msg("regex", messages, field=field)

            # 12. URL
            if name == "url":
                val = str(self.data.get(field, ""))
                # SECURITY: Limit input length to prevent ReDoS attacks
                if val and (len(val) > _MAX_REGEX_INPUT_LENGTH or not _RE_URL.match(val)):
                    self.errors[field] = self._msg("url", messages, field=field)

            # 13. Date (ISO format YYYY-MM-DD)
            if name == "date":
                val = str(self.data.get(field, ""))
                if val:
                    try:
                        datetime.date.fromisoformat(val)
                    except ValueError:
                        self.errors[field] = self._msg("date", messages, field=field)

            # required_if:other_field,value
            if name == "required_if" and arg:
                other, expected = arg.split(",", 1)
                if str(self.data.get(other, "")) == expected:
                    val = self.data.get(field)
                    if val is None or str(val).strip() == "":
                        self.errors[field] = self._msg("required_if", messages, field=field)

            # required_with:other_field
            if name == "required_with" and arg:
                if self.data.get(arg):
                    val = self.data.get(field)
                    if val is None or str(val).strip() == "":
                        self.errors[field] = self._msg("required_with", messages, field=field)

            # between:min,max (numeric range)
            if name == "between" and arg:
                try:
                    lo, hi = [float(x) for x in arg.split(",")]
                    val = self.data.get(field, "")
                    if val != "" and not (lo <= float(val) <= hi):
                        self.errors[field] = self._msg("between", messages, arg, field)
                except (ValueError, TypeError):
                    pass

            # numeric
            if name == "numeric":
                val = self.data.get(field)
                if val is not None and val != "":
                    try:
                        float(str(val))
                    except ValueError:
                        self.errors[field] = self._msg("numeric", messages, field=field)

            # digits:N
            if name == "digits" and arg:
                val = str(self.data.get(field, ""))
                if val and (not val.isdigit() or len(val) != int(arg)):
                    self.errors[field] = self._msg("digits", messages, arg, field)

            # url
            if name == "url":
                val = str(self.data.get(field, ""))
                if val and (len(val) > _MAX_REGEX_INPUT_LENGTH or not _RE_URL.match(val)):
                    self.errors[field] = self._msg("url", messages, field=field)

            # regex:pattern
            if name == "regex" and arg:
                val = str(self.data.get(field, ""))
                if val:
                    if len(val) > _MAX_REGEX_INPUT_LENGTH:
                         self.errors[field] = self._msg("regex", messages, field=field)
                    else:
                        try:
                            if arg not in _regex_cache:
                                _regex_cache[arg] = re.compile(arg)
                            if not _regex_cache[arg].match(val):
                                self.errors[field] = self._msg("regex", messages, field=field)
                        except re.error:
                            pass

            # boolean
            if name == "boolean":
                val = self.data.get(field)
                if val is not None and val != "":
                    s_val = str(val).lower()
                    if s_val not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
                        self.errors[field] = self._msg("boolean", messages, field=field)

            # slug
            if name == "slug":
                val = str(self.data.get(field, ""))
                if val and not re.match(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", val):
                    self.errors[field] = self._msg("slug", messages, field=field)

            # uuid
            if name == "uuid":
                val = str(self.data.get(field, ""))
                if val:
                    pattern = r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$"
                    if not re.match(pattern, val, re.I):
                        self.errors[field] = self._msg("uuid", messages, field=field)

            # alpha
            if name == "alpha":
                val = str(self.data.get(field, ""))
                if val and not val.isalpha():
                    self.errors[field] = self._msg("alpha", messages, field=field)

            # alpha_num
            if name == "alpha_num":
                val = str(self.data.get(field, ""))
                if val and not val.isalnum():
                    self.errors[field] = self._msg("alpha_num", messages, field=field)

            # tel
            if name == "tel":
                val = str(self.data.get(field, ""))
                # SECURITY: Limit input length to prevent ReDoS attacks
                if val and (len(val) > _MAX_REGEX_INPUT_LENGTH or not _RE_TEL.match(val)):
                    self.errors[field] = self._msg("tel", messages, field=field)

            # color (hex format #RRGGBB or #RGB)
            if name == "color":
                val = str(self.data.get(field, ""))
                if val:
                    # Accept #RRGGBB or #RGB format
                    if not re.match(r"^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})$", val):
                        self.errors[field] = self._msg("color", messages, field=field)

            # 14. Same as another field (same:other_field)
            if name == "same" and arg:
                val = self.data.get(field, "")
                other_val = self.data.get(arg, "")
                if val != other_val:
                    self.errors[field] = self._msg("same", messages, arg, field)

            # 15. JSON
            if name == "json":
                val = self.data.get(field, "")
                if val:
                    try:
                        json_mod.loads(str(val))
                    except (ValueError, TypeError):
                        self.errors[field] = self._msg("json", messages, field=field)

            # Custom registered rule
            if name in _CUSTOM_RULES:
                fn, default_msg = _CUSTOM_RULES[name]
                val = self.data.get(field)
                try:
                    ok = fn(val, arg, self.data)
                except Exception:
                    ok = False
                if not ok:
                    self.errors[field] = messages.get(name, default_msg)

        return len(self.errors) == 0

    def rules(self, schema: dict[str, Union[str, tuple[str, dict[str, str]]]]) -> bool:
        """Apply a full schema of rules to the current data."""
        for field, value in schema.items():
            if isinstance(value, tuple):
                rules, messages = value
                self.rule(field, rules, messages)
            else:
                self.rule(field, value)
        return len(self.errors) == 0

    def validate(self) -> bool:
        """Check if any errors were encountered during validation."""
        return len(self.errors) == 0


class SchemaMeta(type):
    def __new__(mcs, name, bases, attrs):
        if name == "Schema":
            return super().__new__(mcs, name, bases, attrs)
        fields = {k: v for k, v in attrs.items() if isinstance(v, Field)}
        attrs["_fields"] = fields
        for k in fields:
            attrs.pop(k)
        return super().__new__(mcs, name, bases, attrs)


class Schema(metaclass=SchemaMeta):
    """Base class for defining structured data schemas for serialization and deserialization."""

    def __init__(self, many: bool = False):
        """Initialize the schema.

        Args:
            many: If True, the schema expects a list of objects/dicts.
        """
        self.many = many

    def dump(
        self, obj: Union[Any, list[Any]]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """Serialize an object or list of objects into a dictionary representation."""
        if self.many:
            return [self._serialize(item) for item in obj]
        return self._serialize(obj)

    def _serialize(self, obj: Any) -> dict[str, Any]:
        """Perform recursive serialization on a single object instance."""
        data = {}
        for field_name in self._fields:
            if hasattr(obj, field_name):
                value = getattr(obj, field_name)
            elif isinstance(obj, dict):
                value = obj.get(field_name)
            else:
                value = None

            if isinstance(value, (datetime.date, datetime.datetime)):
                data[field_name] = value.isoformat()
            else:
                data[field_name] = value
        return data

    def load(
        self, data: Union[dict[str, Any], list[dict[str, Any]]]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """Deserialize external data into a clean dictionary or list of dictionaries."""
        if self.many:
            return [self._deserialize(item) for item in data]
        return self._deserialize(data)

    def _deserialize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Clean and filter external data based on schema fields."""
        clean_data = {}
        for field_name in self._fields:
            clean_data[field_name] = data.get(field_name)
        return clean_data
