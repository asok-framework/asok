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

_DEFAULT_MESSAGES = {
    "required": "This field is required.",
    "email": "Invalid email address.",
    "min": "Minimum {arg} characters.",
    "max": "Maximum {arg} characters.",
    "unique": "This value is already taken.",
    "ext": "Allowed extensions: {arg}.",
    "size": "File too large (max {arg}).",
    "confirmed": "Confirmation does not match.",
    "in": "Must be one of: {arg}.",
    "numeric": "Must be a number.",
    "regex": "Invalid format.",
    "url": "Invalid URL.",
    "date": "Invalid date format.",
    "same": "Must match {arg}.",
    "between": "Must be between {arg}.",
    "alpha": "Only letters allowed.",
    "alpha_num": "Only letters and numbers allowed.",
    "required_if": "This field is required.",
    "required_with": "This field is required.",
    "json": "Invalid JSON format.",
    "tel": "Invalid telephone number.",
}


_CUSTOM_RULES = {}


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
        self, rule_name: str, messages: dict[str, str], arg: Optional[Any] = None
    ) -> str:
        """Resolve and format the error message for a specific rule failure."""
        if rule_name in messages:
            return messages[rule_name]
        default = _DEFAULT_MESSAGES.get(rule_name, "Invalid value.")
        text = self._t(f"v_{rule_name}") if self._t else default
        # If translation key not found, _t returns the key itself — fall back to default
        if text == f"v_{rule_name}":
            text = default
        if arg and "{arg}" in text:
            text = text.replace("{arg}", str(arg))
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
                        self.errors[field] = self._msg("required", messages)

            # 2. Email
            if name == "email":
                val = self.data.get(field)
                if val and not _RE_EMAIL.match(str(val)):
                    self.errors[field] = self._msg("email", messages)

            # 3. Min Length
            if name == "min" and arg:
                val = self.data.get(field, "")
                if len(str(val)) < int(arg):
                    self.errors[field] = self._msg("min", messages, arg)

            # 4. Max Length
            if name == "max" and arg:
                val = self.data.get(field, "")
                if len(str(val)) > int(arg):
                    self.errors[field] = self._msg("max", messages, arg)

            # 5. Unique (format unique:Model,field)
            if name == "unique" and arg:
                model_name, field_name = arg.split(",")
                model = MODELS_REGISTRY.get(model_name)
                if model:
                    val = self.data.get(field)
                    if model.find(**{field_name: val}):
                        self.errors[field] = self._msg("unique", messages)

            # 6. File Extensions (ext:jpg,png)
            if name == "ext" and arg:
                f = self.files.get(field)
                if f:
                    exts = arg.split(",")
                    filename = getattr(f, "filename", "").lower()
                    if not any(filename.endswith("." + e) for e in exts):
                        self.errors[field] = self._msg("ext", messages, arg)

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
                            self.errors[field] = self._msg("size", messages, arg)
                    except ValueError:
                        pass

            # 8. Confirmed
            if name == "confirmed":
                val = self.data.get(field, "")
                confirm_val = self.data.get(f"{field}_confirmation", "")
                if val != confirm_val:
                    self.errors[field] = self._msg("confirmed", messages)

            # 9. In list (in:a,b,c)
            if name == "in" and arg:
                val = str(self.data.get(field, ""))
                allowed = arg.split(",")
                if val not in allowed:
                    self.errors[field] = self._msg("in", messages, arg)

            # 10. Numeric
            if name == "numeric":
                val = self.data.get(field, "")
                if (
                    val
                    and not str(val).replace(".", "", 1).replace("-", "", 1).isdigit()
                ):
                    self.errors[field] = self._msg("numeric", messages)

            # 11. Regex (regex:pattern)
            if name == "regex" and arg:
                val = str(self.data.get(field, ""))
                if val:
                    pattern = _regex_cache.get(arg)
                    if pattern is None:
                        try:
                            pattern = re.compile(arg)
                            _regex_cache[arg] = pattern
                        except re.error:
                            self.errors[field] = self._msg("regex", messages)
                            continue
                    if not pattern.match(val):
                        self.errors[field] = self._msg("regex", messages)

            # 12. URL
            if name == "url":
                val = str(self.data.get(field, ""))
                if val and not _RE_URL.match(val):
                    self.errors[field] = self._msg("url", messages)

            # 13. Date (ISO format YYYY-MM-DD)
            if name == "date":
                val = str(self.data.get(field, ""))
                if val:
                    try:
                        datetime.date.fromisoformat(val)
                    except ValueError:
                        self.errors[field] = self._msg("date", messages)

            # required_if:other_field,value
            if name == "required_if" and arg:
                other, expected = arg.split(",", 1)
                if str(self.data.get(other, "")) == expected:
                    val = self.data.get(field)
                    if val is None or str(val).strip() == "":
                        self.errors[field] = self._msg("required_if", messages)

            # required_with:other_field
            if name == "required_with" and arg:
                if self.data.get(arg):
                    val = self.data.get(field)
                    if val is None or str(val).strip() == "":
                        self.errors[field] = self._msg("required_with", messages)

            # between:min,max (numeric range)
            if name == "between" and arg:
                try:
                    lo, hi = [float(x) for x in arg.split(",")]
                    val = self.data.get(field, "")
                    if val != "" and not (lo <= float(val) <= hi):
                        self.errors[field] = self._msg("between", messages, arg)
                except (ValueError, TypeError):
                    pass

            # alpha
            if name == "alpha":
                val = str(self.data.get(field, ""))
                if val and not val.isalpha():
                    self.errors[field] = self._msg("alpha", messages)

            # alpha_num
            if name == "alpha_num":
                val = str(self.data.get(field, ""))
                if val and not val.isalnum():
                    self.errors[field] = self._msg("alpha_num", messages)

            # tel
            if name == "tel":
                val = str(self.data.get(field, ""))
                if val and not _RE_TEL.match(val):
                    self.errors[field] = self._msg("tel", messages)

            # 14. Same as another field (same:other_field)
            if name == "same" and arg:
                val = self.data.get(field, "")
                other_val = self.data.get(arg, "")
                if val != other_val:
                    self.errors[field] = self._msg("same", messages, arg)

            # 15. JSON
            if name == "json":
                val = self.data.get(field, "")
                if val:
                    try:
                        json_mod.loads(str(val))
                    except (ValueError, TypeError):
                        self.errors[field] = self._msg("json", messages)

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
