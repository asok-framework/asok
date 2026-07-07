from __future__ import annotations

from html import escape
from typing import Any, Callable, Optional, Union

from asok.templates import SafeString, _render_attrs

from . import render
from .utils import Renderable, _merge_attrs

_LENGTH_ATTR_TYPES = {
    "text",
    "email",
    "password",
    "url",
    "search",
    "tel",
    "textarea",
}
_RANGE_ATTR_TYPES = {
    "number",
    "range",
    "date",
    "datetime-local",
    "time",
    "month",
    "week",
}
_PATTERN_TYPES = {"text", "email", "password", "url", "search", "tel"}
_HTML5_VALIDATABLE_TYPES = (
    _LENGTH_ATTR_TYPES
    | _RANGE_ATTR_TYPES
    | {"select", "checkbox", "radio", "file", "color"}
)


class FormField:
    """Represents a single field within a Form, including its value, validation errors, and rendering logic."""

    def __init__(
        self,
        name: str,
        label: str,
        field_type: str,
        rules: str = "",
        messages: Optional[Union[str, dict[str, str]]] = None,
        choices: Optional[
            Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]
        ] = None,
        **attrs: Any,
    ):
        # SECURITY: Validate field name to prevent injection in HTML attributes
        import re

        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
            raise ValueError(
                f"Invalid field name '{name}': must start with letter/underscore "
                f"and contain only alphanumeric characters and underscores"
            )
        self.name: str = name
        self._label: str = label
        self.type: str = field_type
        self.rules: str = rules
        self.messages = self._parse_messages(messages, rules)
        self._choices: Optional[
            Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]
        ] = choices

        # Dropdown specific data
        self._items: Any = attrs.pop("items", None)
        self.item_meta = {
            "title": attrs.pop("title", "name"),
            "subtitle": attrs.pop("subtitle", None),
            "image": attrs.pop("image", None),
            "searchable": attrs.pop("searchable", True),
        }

        attrs = dict(attrs)  # never mutate the schema dict (shared in templates)
        self.readonly: bool = attrs.pop("readonly", False)
        self.attrs: dict[str, Any] = attrs
        self.value: Any = ""
        self._error: str = ""

    def _parse_messages(
        self, messages: Optional[Union[str, dict[str, str]]], rules: str
    ) -> dict[str, str]:
        if not isinstance(messages, str):
            return messages or {}
        return self._parse_string_messages(messages, rules)

    def _parse_string_messages(self, messages: str, rules: str) -> dict[str, str]:
        rule_names = [r.split(":")[0] for r in rules.split("|")] if rules else []
        return {r: messages for r in rule_names}

    @property
    def choices(self) -> Optional[list[tuple[Any, str]]]:
        """Get the field's choices, resolving them if they are dynamic (callable)."""
        if callable(self._choices):
            try:
                return self._choices()
            except Exception:
                return []
        return self._choices

    @choices.setter
    def choices(
        self,
        value: Optional[
            Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]
        ],
    ) -> None:
        """Set the field's choices."""
        self._choices = value

    @property
    def items(self) -> Any:
        """Get the field's items, resolving them if they are dynamic (callable)."""
        if callable(self._items):
            try:
                return self._items()
            except Exception:
                return []
        return self._items

    @items.setter
    def items(self, value: Any) -> None:
        """Set the field's items."""
        self._items = value

    @property
    def error(self) -> Renderable:
        """Render the field's validation error message."""
        return Renderable(self.render_error)

    @property
    def label(self) -> Renderable:
        """Render the field's HTML label."""
        return Renderable(self.render_label)

    @property
    def input(self) -> Renderable:
        """Render the field's HTML input element."""
        return Renderable(self.render_input)

    def render_label(self, **overrides: Any) -> str:
        """Internal method for rendering the <label> HTML."""
        if self.type == "hidden":
            return ""
        attrs = _merge_attrs({"for": self.name}, overrides)
        return f"<label{_render_attrs(attrs)}>{escape(self._label)}</label>"

    def _format_value_for_input(self) -> str:
        """Format value appropriately for HTML5 input types."""
        if self.value is None or self.value == "":
            return ""
        return self._dispatch_format(str(self.value))

    def _dispatch_format(self, val_str: str) -> str:
        if self.type == "date":
            return self._format_date(val_str)
        if self.type == "datetime-local":
            return self._format_datetime_local(val_str)
        if self.type == "time":
            return self._format_time(val_str)
        return val_str

    def _format_date(self, val_str: str) -> str:
        if "T" in val_str:
            return val_str.split("T")[0]
        elif " " in val_str:
            return val_str.split(" ")[0]
        return val_str[:10] if len(val_str) >= 10 else val_str

    def _format_datetime_local(self, val_str: str) -> str:
        val_str = val_str.replace(" ", "T")
        if "T" not in val_str:
            return val_str
        parts = val_str.split("T")
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else "00:00"
        time_part = time_part.split("+")[0].split("-")[0].split("Z")[0]
        time_parts = time_part.split(":")
        time_part = ":".join(time_parts[:2]) if len(time_parts) >= 2 else time_part
        return f"{date_part}T{time_part}"

    def _format_time(self, val_str: str) -> str:
        if "T" in val_str:
            val_str = val_str.split("T")[1]
        elif " " in val_str:
            val_str = val_str.split(" ")[1]
        time_parts = val_str.split(":")
        return ":".join(time_parts[:2]) if len(time_parts) >= 2 else val_str

    def _render_readonly(self, val: str) -> str:
        display = val if val else "—"
        return f'<div class="readonly-value">{display}</div>'

    def _render_input_fallback(self, val: str, merged: dict[str, Any]) -> str:
        attrs = {"type": self.type, "id": self.name, "name": self.name, **merged}
        if self.type != "file":
            attrs["value"] = val
        return f"<input{_render_attrs(attrs)}>"

    def _apply_min_max_attr(
        self, range_key: str, length_key: str, val: str, attrs: dict[str, Any]
    ) -> None:
        if self.type in _LENGTH_ATTR_TYPES:
            attrs[length_key] = val
        elif self.type in _RANGE_ATTR_TYPES:
            attrs[range_key] = val

    def _apply_ranged_rule_attr(
        self, name: str, arg: str, attrs: dict[str, Any]
    ) -> None:
        if name == "min":
            self._apply_min_max_attr("min", "minlength", arg, attrs)
        elif name == "max":
            self._apply_min_max_attr("max", "maxlength", arg, attrs)
        elif name == "between" and "," in arg:
            lo, _, hi = arg.partition(",")
            lo, hi = lo.strip(), hi.strip()
            self._apply_min_max_attr("min", "minlength", lo, attrs)
            self._apply_min_max_attr("max", "maxlength", hi, attrs)

    def _apply_accepted_rule_attr(self, name: str, attrs: dict[str, Any]) -> bool:
        if name == "accepted" and self.type == "checkbox":
            attrs["required"] = True
            return True
        return False

    def _apply_regex_rule_attr(
        self, name: str, arg: str, attrs: dict[str, Any]
    ) -> bool:
        if name == "regex" and arg and self.type in _PATTERN_TYPES:
            attrs.setdefault("pattern", arg)
            return True
        return False

    def _apply_single_rule_attr(
        self, name: str, arg: str, attrs: dict[str, Any]
    ) -> None:
        if name == "required":
            attrs["required"] = True
        elif self._apply_accepted_rule_attr(name, attrs):
            pass
        elif self._apply_regex_rule_attr(name, arg, attrs):
            pass
        elif arg:
            self._apply_ranged_rule_attr(name, arg, attrs)

    def _rules_to_html_attrs(self) -> dict[str, Any]:
        """Translate validation rules into HTML5 attributes for client-side enforcement.

        Composite widgets (dropdown, phone, daterange, ...) carry their hidden
        input separately and are skipped — their visible elements aren't the
        target for these attributes.
        """
        if not self.rules or self.type not in _HTML5_VALIDATABLE_TYPES:
            return {}
        attrs: dict[str, Any] = {}
        for raw in self.rules.split("|"):
            name, _, arg = raw.strip().partition(":")
            self._apply_single_rule_attr(name.strip(), arg.strip(), attrs)
        return attrs

    def render_input(self, **overrides: Any) -> str:
        """Internal method for rendering the HTML input element (input, select, or textarea)."""
        val = escape(self._format_value_for_input())

        if self.readonly:
            return self._render_readonly(val)

        base = self._rules_to_html_attrs()
        base.update(self.attrs)
        if self._error:
            existing = base.get("class", "")
            base["class"] = f"{existing} input-error".strip()

        merged = _merge_attrs(base, overrides)

        renderer = getattr(render, f"render_{self.type}", None)
        if renderer is not None:
            if self.type == "dropdown":
                return renderer(self, val, merged, overrides)
            return renderer(self, val, merged)

        return self._render_input_fallback(val, merged)

    def render_error(self, **overrides: Any) -> str:
        """Internal method for rendering the error message <div>."""
        if not self._error:
            return ""
        attrs = _merge_attrs({"class": "form-error"}, overrides)
        return f"<div{_render_attrs(attrs)}>{escape(self._error)}</div>"

    def render(self, **overrides: Any) -> str:
        """Render the complete form group (label, input, error)."""
        if self.type == "hidden":
            return self.render_input(**overrides)
        if self.type == "title":
            attrs = _merge_attrs({"class": "form-title"}, overrides)
            return f"<h3{_render_attrs(attrs)}>{escape(self._label)}</h3>"
        if self.type == "checkbox":
            return (
                f'<div class="form-group">'
                f"<label>{self.render_input(**overrides)} {escape(self._label)}</label>"
                f"{self.render_error()}"
                f"</div>"
            )
        return (
            f'<div class="form-group">'
            f"{self.render_label()}"
            f"{self.render_input(**overrides)}"
            f"{self.render_error()}"
            f"</div>"
        )

    def __call__(self, **overrides: Any) -> SafeString:
        return SafeString(self.render(**overrides))

    def __str__(self) -> str:
        return SafeString(self.render())
