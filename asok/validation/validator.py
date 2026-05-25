from __future__ import annotations

import datetime
import json as json_mod
from typing import Any, Callable, Optional, Union

from . import rules as rules_mod
from .interpolation import _DEFAULT_MESSAGES, _interpolate
from .registry import _CUSTOM_RULES


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
        self,
        rule_name: str,
        messages: dict[str, str],
        arg: Optional[Any] = None,
        field: Optional[str] = None,
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
                f = self.files.get(field)
                if not rules_mod.check_required(val, f):
                    self.errors[field] = self._msg("required", messages, field=field)

            # 2. Email
            elif name == "email":
                val = self.data.get(field)
                if not rules_mod.check_email(val):
                    self.errors[field] = self._msg("email", messages, field=field)

            # 3. Min Length
            elif name == "min" and arg:
                val = self.data.get(field, "")
                if not rules_mod.check_min(val, arg):
                    self.errors[field] = self._msg("min", messages, arg, field)

            # 4. Max Length
            elif name == "max" and arg:
                val = self.data.get(field, "")
                if not rules_mod.check_max(val, arg):
                    self.errors[field] = self._msg("max", messages, arg, field)

            # 5. Unique (format unique:Model,field)
            elif name == "unique" and arg:
                val = self.data.get(field)
                try:
                    model_name, field_name = arg.split(",")
                    if not rules_mod.check_unique(val, model_name, field_name):
                        self.errors[field] = self._msg("unique", messages, field=field)
                except ValueError:
                    pass

            # 6. File Extensions (ext:jpg,png)
            elif name == "ext" and arg:
                f = self.files.get(field)
                if not rules_mod.check_ext(f, arg):
                    self.errors[field] = self._msg("ext", messages, arg, field)

            # 7. File Size (size:2M)
            elif name == "size" and arg:
                f = self.files.get(field)
                if not rules_mod.check_size(f, arg):
                    self.errors[field] = self._msg("size", messages, arg, field)

            # 8. Confirmed
            elif name == "confirmed":
                val = self.data.get(field, "")
                confirm_val = self.data.get(f"{field}_confirmation", "")
                if not rules_mod.check_confirmed(val, confirm_val):
                    self.errors[field] = self._msg("confirmed", messages, field=field)

            # 9. In list (in:a,b,c)
            elif name == "in" and arg:
                val = self.data.get(field, "")
                if not rules_mod.check_in(val, arg):
                    self.errors[field] = self._msg("in", messages, arg, field)

            # 10. Numeric
            elif name == "numeric":
                val = self.data.get(field, "")
                if not rules_mod.check_numeric(val):
                    self.errors[field] = self._msg("numeric", messages, field=field)

            # 11. Regex (regex:pattern)
            elif name == "regex" and arg:
                val = self.data.get(field, "")
                if not rules_mod.check_regex(val, arg):
                    self.errors[field] = self._msg("regex", messages, field=field)

            # 12. URL
            elif name == "url":
                val = self.data.get(field, "")
                if not rules_mod.check_url(val):
                    self.errors[field] = self._msg("url", messages, field=field)

            # 13. Date (ISO format YYYY-MM-DD)
            elif name == "date":
                val = self.data.get(field, "")
                if not rules_mod.check_date(val):
                    self.errors[field] = self._msg("date", messages, field=field)

            # required_if:other_field,value
            elif name == "required_if" and arg:
                try:
                    other, expected = arg.split(",", 1)
                    val = self.data.get(field)
                    other_val = self.data.get(other, "")
                    if not rules_mod.check_required_if(val, other_val, expected):
                        self.errors[field] = self._msg(
                            "required_if", messages, field=field
                        )
                except ValueError:
                    pass

            # required_with:other_field
            elif name == "required_with" and arg:
                val = self.data.get(field)
                other_val = self.data.get(arg)
                if not rules_mod.check_required_with(val, other_val):
                    self.errors[field] = self._msg(
                        "required_with", messages, field=field
                    )

            # between:min,max (numeric range)
            elif name == "between" and arg:
                val = self.data.get(field, "")
                try:
                    lo, hi = arg.split(",")
                    if not rules_mod.check_between(val, lo, hi):
                        self.errors[field] = self._msg("between", messages, arg, field)
                except ValueError:
                    pass

            # digits:N
            elif name == "digits" and arg:
                val = self.data.get(field, "")
                if not rules_mod.check_digits(val, arg):
                    self.errors[field] = self._msg("digits", messages, arg, field)

            # boolean
            elif name == "boolean":
                val = self.data.get(field)
                if not rules_mod.check_boolean(val):
                    self.errors[field] = self._msg("boolean", messages, field=field)

            # slug
            elif name == "slug":
                val = self.data.get(field, "")
                if not rules_mod.check_slug(val):
                    self.errors[field] = self._msg("slug", messages, field=field)

            # uuid
            elif name == "uuid":
                val = self.data.get(field, "")
                if not rules_mod.check_uuid(val):
                    self.errors[field] = self._msg("uuid", messages, field=field)

            # alpha
            elif name == "alpha":
                val = self.data.get(field, "")
                if not rules_mod.check_alpha(val):
                    self.errors[field] = self._msg("alpha", messages, field=field)

            # alpha_num
            elif name == "alpha_num":
                val = self.data.get(field, "")
                if not rules_mod.check_alpha_num(val):
                    self.errors[field] = self._msg("alpha_num", messages, field=field)

            # tel
            elif name == "tel":
                val = self.data.get(field, "")
                if not rules_mod.check_tel(val):
                    self.errors[field] = self._msg("tel", messages, field=field)

            # image
            elif name == "image":
                f = self.files.get(field)
                if not rules_mod.check_image(f):
                    self.errors[field] = self._msg("image", messages, field=field)

            # password_strength
            elif name == "password_strength":
                val = self.data.get(field, "")
                if not rules_mod.check_password_strength(val):
                    self.errors[field] = self._msg(
                        "password_strength", messages, field=field
                    )

            # color (hex format #RRGGBB or #RGB)
            elif name == "color":
                val = self.data.get(field, "")
                if not rules_mod.check_color(val):
                    self.errors[field] = self._msg("color", messages, field=field)

            # month (YYYY-MM format)
            elif name == "month":
                val = self.data.get(field, "")
                if not rules_mod.check_month(val):
                    self.errors[field] = self._msg("month", messages, field=field)

            # base64 (validates base64 encoded data, especially for images)
            elif name == "base64":
                val = self.data.get(field, "")
                if not rules_mod.check_base64(val):
                    self.errors[field] = self._msg("base64", messages, field=field)

            # 14. Same as another field (same:other_field)
            elif name == "same" and arg:
                val = self.data.get(field, "")
                other_val = self.data.get(arg, "")
                if not rules_mod.check_same(val, other_val):
                    self.errors[field] = self._msg("same", messages, arg, field)

            # 15. JSON
            elif name == "json":
                val = self.data.get(field, "")
                if not rules_mod.check_json(val):
                    self.errors[field] = self._msg("json", messages, field=field)

            # daterange validation
            elif name == "daterange":
                val = self.data.get(field)
                if val:
                    try:
                        if isinstance(val, str):
                            d = json_mod.loads(val)
                        else:
                            d = val
                        start = d.get("start")
                        end = d.get("end")
                        if start and end:
                            ds = datetime.date.fromisoformat(start)
                            de = datetime.date.fromisoformat(end)
                            if de < ds:
                                self.errors[field] = self._msg(
                                    "daterange_order", messages, field=field
                                )
                            if arg == "future" and ds < datetime.date.today():
                                self.errors[field] = self._msg(
                                    "daterange_future", messages, field=field
                                )
                        elif (start or end) and name == "required":
                            self.errors[field] = self._msg(
                                "daterange_invalid", messages, field=field
                            )
                    except (
                        ValueError,
                        json_mod.JSONDecodeError,
                        TypeError,
                        AttributeError,
                    ):
                        self.errors[field] = self._msg(
                            "daterange_invalid", messages, field=field
                        )

            # Custom registered rule
            elif name in _CUSTOM_RULES:
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
