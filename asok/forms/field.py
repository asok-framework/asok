from __future__ import annotations

from html import escape
from typing import Any, Callable, Optional, Union

from asok.templates import SafeString, _render_attrs

from . import render
from .utils import Renderable, _merge_attrs


class FormField:
    """Represents a single field within a Form, including its value, validation errors, and rendering logic."""

    def __init__(
        self,
        name: str,
        label: str,
        field_type: str,
        rules: str = "",
        messages: Optional[Union[str, dict[str, str]]] = None,
        choices: Optional[Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]] = None,
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
        if isinstance(messages, str):
            rule_names = [r.split(":")[0] for r in rules.split("|")] if rules else []
            self.messages: dict[str, str] = {r: messages for r in rule_names}
        else:
            self.messages: dict[str, str] = messages or {}
        self._choices: Optional[Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]] = choices

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
    def choices(self, value: Optional[Union[list[tuple[Any, str]], Callable[[], list[tuple[Any, str]]]]]) -> None:
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
        if not self.value:
            return ""

        val_str = str(self.value)

        # Format for HTML5 date/time inputs
        if self.type == "date":
            # Convert "YYYY-MM-DD HH:MM:SS" or "YYYY-MM-DDTHH:MM:SS" to "YYYY-MM-DD"
            if "T" in val_str:
                return val_str.split("T")[0]
            elif " " in val_str:
                return val_str.split(" ")[0]
            return val_str[:10] if len(val_str) >= 10 else val_str

        elif self.type == "datetime-local":
            # Convert to "YYYY-MM-DDTHH:MM" (no seconds, no timezone)
            val_str = val_str.replace(" ", "T")  # Convert space to T
            # Remove seconds, microseconds, and timezone
            if "T" in val_str:
                parts = val_str.split("T")
                date_part = parts[0]
                time_part = parts[1] if len(parts) > 1 else "00:00"
                # Remove timezone info if present (+00:00, Z, etc.)
                time_part = time_part.split("+")[0].split("-")[0].split("Z")[0]
                # Keep only HH:MM
                time_parts = time_part.split(":")
                time_part = (
                    ":".join(time_parts[:2]) if len(time_parts) >= 2 else time_part
                )
                return f"{date_part}T{time_part}"
            return val_str

        elif self.type == "time":
            # Convert to "HH:MM"
            if "T" in val_str:
                val_str = val_str.split("T")[1]
            elif " " in val_str:
                val_str = val_str.split(" ")[1]
            # Remove seconds and keep only HH:MM
            time_parts = val_str.split(":")
            return ":".join(time_parts[:2]) if len(time_parts) >= 2 else val_str

        return val_str

    def render_input(self, **overrides: Any) -> str:
        """Internal method for rendering the HTML input element (input, select, or textarea)."""
        esc = escape
        val = esc(self._format_value_for_input())

        if self.readonly:
            display = val if val else "—"
            return f'<div class="readonly-value">{display}</div>'

        base = dict(self.attrs)
        if self._error:
            existing = base.get("class", "")
            base["class"] = f"{existing} input-error".strip()

        merged = _merge_attrs(base, overrides)

        if self.type == "textarea":
            return render.render_textarea(self, val, merged)
        if self.type == "select":
            return render.render_select(self, val, merged)
        if self.type == "checkbox":
            return render.render_checkbox(self, val, merged)
        if self.type == "radio":
            return render.render_radio(self, val, merged)
        if self.type == "dropdown":
            return render.render_dropdown(self, val, merged, overrides)
        if self.type == "image":
            return render.render_image(self, val, merged)
        if self.type == "tags":
            return render.render_tags(self, val, merged)
        if self.type == "daterange":
            return render.render_daterange(self, val, merged)
        if self.type == "toggle":
            return render.render_toggle(self, val, merged)
        if self.type == "otp":
            return render.render_otp(self, val, merged)
        if self.type == "month":
            return render.render_month(self, val, merged)
        if self.type == "rating":
            return render.render_rating(self, val, merged)
        if self.type == "timerange":
            return render.render_timerange(self, val, merged)
        if self.type == "files":
            return render.render_files(self, val, merged)
        if self.type == "autocomplete":
            return render.render_autocomplete(self, val, merged)
        if self.type == "cascading":
            return render.render_cascading(self, val, merged)
        if self.type == "phone":
            return render.render_phone(self, val, merged)
        if self.type == "wysiwyg":
            return render.render_wysiwyg(self, val, merged)
        if self.type == "dropzone":
            return render.render_dropzone(self, val, merged)
        if self.type == "signature":
            return render.render_signature(self, val, merged)
        if self.type == "transfer":
            return render.render_transfer(self, val, merged)
        if self.type == "treeselect":
            return render.render_treeselect(self, val, merged)

        attrs = {"type": self.type, "id": self.name, "name": self.name, **merged}
        if self.type != "file":
            attrs["value"] = val
        return f"<input{_render_attrs(attrs)}>"

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
