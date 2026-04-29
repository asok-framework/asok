from __future__ import annotations

import enum
from html import escape
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

from .orm import Model
from .templates import SafeString
from .validation import Validator

if TYPE_CHECKING:
    from .request import Request


def _merge_attrs(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Merge base attributes with overrides, stripping trailing underscores from keys."""
    merged = dict(base)
    for k, v in overrides.items():
        key = k.rstrip("_") if k.endswith("_") and k != "_" else k
        merged[key] = v
    return merged


def _render_attrs(attrs: dict[str, Any]) -> str:
    """Render a dictionary of attributes into a space-separated HTML string."""
    parts = ""
    for k, v in attrs.items():
        if v is True:
            parts += f" {escape(k)}"
        elif v is not False and v is not None:
            parts += f' {escape(k)}="{escape(str(v))}"'
    return parts


class Renderable:
    """A lazy-rendering wrapper for HTML elements.

    Supports both direct string conversion: `{{ field.label }}`
    and parameter-based invocation: `{{ field.label(class='btn') }}`.
    """

    def __init__(self, render_fn: Callable[..., str]):
        self._render_fn = render_fn

    def __str__(self) -> str:
        return SafeString(self._render_fn())

    def __call__(self, **attrs: Any) -> SafeString:
        return SafeString(self._render_fn(**attrs))


class FormField:
    """Represents a single field within a Form, including its value, validation errors, and rendering logic."""

    def __init__(
        self,
        name: str,
        label: str,
        field_type: str,
        rules: str = "",
        messages: Optional[Union[str, dict[str, str]]] = None,
        choices: Optional[list[tuple[Any, str]]] = None,
        **attrs: Any,
    ):
        self.name: str = name
        self._label: str = label
        self.type: str = field_type
        self.rules: str = rules
        if isinstance(messages, str):
            rule_names = [r.split(":")[0] for r in rules.split("|")] if rules else []
            self.messages: dict[str, str] = {r: messages for r in rule_names}
        else:
            self.messages: dict[str, str] = messages or {}
        self.choices: Optional[list[tuple[Any, str]]] = choices
        attrs = dict(attrs)  # never mutate the schema dict (shared in templates)
        self.readonly: bool = attrs.pop("readonly", False)
        self.attrs: dict[str, Any] = attrs
        self.value: Any = ""
        self._error: str = ""

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
            attrs = {"id": self.name, "name": self.name, **merged}
            return f"<textarea{_render_attrs(attrs)}>{val}</textarea>"

        if self.type == "select":
            attrs = {"id": self.name, "name": self.name, **merged}
            options_html = ""
            for opt_val, opt_label in self.choices or []:
                sel = " selected" if str(opt_val) == str(self.value) else ""
                options_html += f'<option value="{esc(str(opt_val))}"{sel}>{esc(str(opt_label))}</option>'
            return f"<select{_render_attrs(attrs)}>{options_html}</select>"

        if self.type == "checkbox":
            attrs = {"type": "checkbox", "id": self.name, "name": self.name, **merged}
            if self.value and self.value != "0":
                attrs["checked"] = True
            return f"<input{_render_attrs(attrs)}>"

        if self.type == "radio":
            html = ""
            for opt_val, opt_label in self.choices or []:
                radio_attrs = {
                    "type": "radio",
                    "name": self.name,
                    "value": str(opt_val),
                    **merged,
                }
                radio_attrs.pop("id", None)
                if str(opt_val) == str(self.value):
                    radio_attrs["checked"] = True
                html += f"<label><input{_render_attrs(radio_attrs)}> {esc(str(opt_label))}</label>"
            return html

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


class Form:
    """A powerful, schema-based form handling system.

    Supports automatic validation, model pre-filling, and HTML rendering.
    """

    def __init__(
        self, fields_dict: dict[str, tuple], request: Optional[Request] = None
    ):
        """Initialize a form with a field schema and optional request binding.

        Example:
            form = Form({"name": Form.text("Name", "required")}, request)
        """
        if not isinstance(fields_dict, dict):
            raise TypeError(
                "fields_dict must be a dict; got {}".format(type(fields_dict).__name__)
            )
        if not fields_dict:
            raise ValueError("Form fields_dict cannot be empty.")

        self._request: Optional[Request] = request
        self._schema: dict[str, tuple] = fields_dict
        self._is_template: bool = request is None
        self._fields: dict[str, FormField] = {}

        is_post = bool(request) and request.method == "POST"
        for name, definition in fields_dict.items():
            field_type, label, rules, messages, choices, attrs = definition
            field = FormField(
                name, label, field_type, rules, messages, choices, **attrs
            )
            if is_post and not field.readonly:
                field.value = request.form.get(name, "")
            self._fields[name] = field

    def _bind(self, request: Request) -> Form:
        """Internal helper for creating a bound copy of the form."""
        return Form(self._schema, request)

    def bind(self, request: Request) -> Form:
        """Attach a request to this form instance."""
        self._request = request
        self._is_template = False
        is_post = request.method == "POST"
        for name, field in self._fields.items():
            if is_post and not field.readonly:
                field.value = request.form.get(name, "")
        return self

    def validate(self, request: Optional[Request] = None) -> bool:
        """Run validation rules against the submitted request data.

        Returns True if all fields are valid, False otherwise.
        If the form is not yet bound to a request, it will attempt to bind using the provided request.
        """
        if request is not None and self._request is None:
            self.bind(request)

        if not self._request:
            raise RuntimeError(
                "Form.validate() requires a bound request. "
                "Ensure you accessed the form via request.shared() or called form.bind(request)."
            )

        if self._request.method != "POST":
            return False

        schema = {}
        for name, field in self._fields.items():
            if not field.rules:
                continue
            if field.messages:
                schema[name] = (field.rules, field.messages)
            else:
                schema[name] = field.rules

        # from .validation import Validator

        v = Validator(
            self._request.form, self._request.files, translate=self._request.__
        )
        result = v.rules(schema)

        # Update field-level errors
        for name, error in v.errors.items():
            if name in self._fields:
                self._fields[name]._error = error

        return result

    def reset(self) -> Form:
        """Clear all field values and validation errors for a fresh state.

        Returns the Form instance for easy method chaining in templates.
        """
        for field in self._fields.values():
            field.value = ""
            field._error = ""
        return self

    def fill(self, source: Any) -> Form:
        """Pre-fill field values from a model instance or a dictionary."""
        is_post = self._request and self._request.method == "POST"
        is_dict = isinstance(source, dict)
        for name, field in self._fields.items():
            if is_post and not field.readonly:
                continue
            if is_dict:
                if name in source:
                    field.value = source[name] if source[name] is not None else ""
            else:
                if hasattr(source, name):
                    val = getattr(source, name)
                    field.value = val if val is not None else ""
        return self

    @property
    def errors(self):
        return {name: f._error for name, f in self._fields.items() if f._error}

    @property
    def data(self):
        """Return submitted values as a dict, ready for Model.create(**form.data)."""
        return {name: f.value for name, f in self._fields.items()}

    @property
    def csrf(self) -> SafeString:
        """Render the CSRF token hidden input.

        Requires the form to be bound to a request.
        """
        if not self._request:
            return SafeString("")
        return self._request.csrf_input()

    @property
    def hidden_fields(self) -> SafeString:
        """Render all hidden fields in the form (including CSRF)."""
        html = [str(self.csrf)]
        for field in self._fields.values():
            if field.type == "hidden":
                html.append(str(field))
        return SafeString("\n".join(html))

    def __getattribute__(self, name):
        if not name.startswith("_"):
            fields = super().__getattribute__("_fields")
            if name in fields:
                return fields[name]
        return super().__getattribute__(name)

    @classmethod
    def from_model(
        cls: type[Form],
        model: type[Model],
        request: Optional[Request] = None,
        include_fields: Optional[list[str]] = None,
        exclude_fields: Optional[list[str]] = None,
    ) -> Form:
        """Generate a Form instance automatically from a Model class."""
        if not hasattr(model, "_fields"):
            raise TypeError(
                "Form.from_model() expected a Model class; got {}".format(
                    type(model).__name__
                )
            )

        include = set(include_fields) if include_fields else None
        exclude = set(exclude_fields or [])

        schema = {}
        for name, field in model._fields.items():
            if include is not None and name not in include:
                continue
            if name in exclude:
                continue
            if name == "id":
                continue
            if getattr(field, "is_timestamp", False):
                continue
            if getattr(field, "is_soft_delete", False):
                continue
            if getattr(field, "is_slug", False) and getattr(
                field, "populate_from", None
            ):
                continue
            if getattr(field, "is_vector", False):
                continue
            if getattr(field, "hidden", False) and not getattr(
                field, "is_password", False
            ):
                continue
            if getattr(field, "protected", False) and name != "password":
                # Protected fields like 'is_admin' should not be in auto-forms
                # We allow 'password' because it's protected but necessary for signups
                continue

            label = name.replace("_", " ").title()
            rules_parts = []
            is_password = getattr(field, "is_password", False)
            if not field.nullable and not is_password:
                rules_parts.append("required")
            if getattr(field, "is_email", False):
                rules_parts.append("email")
            max_length = getattr(field, "max_length", None)
            if max_length:
                rules_parts.append(f"max:{max_length}")

            rules = "|".join(rules_parts)
            attrs = {}
            if max_length:
                attrs["maxlength"] = max_length

            if is_password:
                schema[name] = cls.password(label, "", **attrs)
            elif getattr(field, "is_file", False):
                schema[name] = cls.file(label, rules, **attrs)
            elif getattr(field, "is_tel", False):
                rules = f"tel|{rules}".strip("|")
                schema[name] = cls.tel(label, rules, **attrs)
            elif getattr(field, "is_url", False):
                rules = f"url|{rules}".strip("|")
                schema[name] = cls.url(label, rules, **attrs)
            elif getattr(field, "is_color", False):
                schema[name] = cls.color(label, rules, **attrs)
            elif getattr(field, "is_time", False):
                schema[name] = cls.time(label, rules, **attrs)
            elif getattr(field, "is_datetime", False):
                schema[name] = cls.datetime_local(label, rules, **attrs)
            elif getattr(field, "is_enum", False):
                schema[name] = cls.enum(label, field.enum_class, rules, **attrs)
            elif getattr(field, "is_json", False):
                schema[name] = cls.json(label, rules, **attrs)
            elif getattr(field, "is_decimal", False):
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, **attrs)
            elif getattr(field, "is_uuid", False):
                attrs["readonly"] = True
                schema[name] = cls.text(label, rules, **attrs)
            elif getattr(field, "is_foreign_key", False):
                target = field.related_model
                try:
                    choices = [(o.id, str(o)) for o in target.all()]
                except Exception:
                    choices = []
                choices = [("", "— None —")] + choices
                schema[name] = cls.select(label, choices, rules, **attrs)
            elif getattr(field, "is_boolean", False):
                schema[name] = cls.checkbox(label, "", **attrs)
            elif field.sql_type == "INTEGER":
                schema[name] = cls.number(label, rules, **attrs)
            elif field.sql_type == "REAL":
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, **attrs)
            elif getattr(field, "is_email", False):
                schema[name] = cls.email(label, rules, **attrs)
            elif getattr(field, "is_text", False):
                schema[name] = cls.textarea(label, rules, **attrs)
            else:
                schema[name] = cls.text(label, rules, **attrs)

        if not schema:
            raise ValueError(
                "Form.from_model({}) produced no fields (check include/exclude).".format(
                    getattr(model, "__name__", "model")
                )
            )

        return cls(schema, request)

    # --- Static factory methods for defining schemas ---

    @staticmethod
    def text(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Standard text input."""
        return ("text", label, rules, messages, None, attrs)

    @staticmethod
    def email(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Email input with browser-side validation."""
        return ("email", label, rules, messages, None, attrs)

    @staticmethod
    def password(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Password input (characters masked)."""
        return ("password", label, rules, messages, None, attrs)

    @staticmethod
    def textarea(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Multi-line text area."""
        return ("textarea", label, rules, messages, None, attrs)

    @staticmethod
    def number(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Numeric input."""
        return ("number", label, rules, messages, None, attrs)

    @staticmethod
    def hidden(rules: str = "", **attrs: Any) -> tuple:
        """Hidden input field."""
        return ("hidden", "", rules, None, None, attrs)

    @staticmethod
    def select(
        label: str,
        choices: list[tuple[Any, str]],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Dropdown selection list."""
        return ("select", label, rules, messages, choices, attrs)

    @staticmethod
    def file(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """File upload input."""
        return ("file", label, rules, messages, None, attrs)

    @staticmethod
    def checkbox(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Single checkbox."""
        return ("checkbox", label, rules, messages, None, attrs)

    @staticmethod
    def radio(
        label: str,
        choices: list[tuple[Any, str]],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Radio button group."""
        return ("radio", label, rules, messages, choices, attrs)

    @staticmethod
    def title(label: str, **attrs: Any) -> tuple:
        """Non-input title element for form organization."""
        return ("title", label, "", None, None, attrs)

    @staticmethod
    def date(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Date picker."""
        return ("date", label, rules, messages, None, attrs)

    @staticmethod
    def datetime_local(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Date and time picker."""
        return ("datetime-local", label, rules, messages, None, attrs)

    @staticmethod
    def time(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Time picker."""
        return ("time", label, rules, messages, None, attrs)

    @staticmethod
    def search(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Search input."""
        return ("search", label, rules, messages, None, attrs)

    @staticmethod
    def url(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """URL input with validation."""
        return ("url", label, rules, messages, None, attrs)

    @staticmethod
    def tel(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Telephone input with validation."""
        return ("tel", label, rules, messages, None, attrs)

    @staticmethod
    def color(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Color picker."""
        return ("color", label, rules, messages, None, attrs)

    @staticmethod
    def range(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Range input (slider)."""
        return ("range", label, rules, messages, None, attrs)

    @staticmethod
    def json(
        label: str,
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """JSON input (renders as a textarea with JSON validation)."""
        real_rules = f"{rules}|json".strip("|")
        return ("textarea", label, real_rules, messages, None, attrs)

    @staticmethod
    def enum(
        label: str,
        enum_class: type[enum.Enum],
        rules: str = "",
        messages: Optional[dict[str, str]] = None,
        **attrs: Any,
    ) -> tuple:
        """Generate a select field from a Python Enum class."""
        choices = [(e.value, e.name.replace("_", " ").title()) for e in enum_class]
        return ("select", label, rules, messages, choices, attrs)
