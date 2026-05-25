from __future__ import annotations

import enum
from typing import Any, Optional

from asok.exceptions import ValidationError
from asok.orm import Model
from asok.request import Request
from asok.templates import SafeString
from asok.validation import Validator

from .field import FormField
from .mixins import SchemaMixin


class Form(SchemaMixin):
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
            attrs = dict(attrs)
            if "choices" in attrs:
                if choices is None:
                    choices = attrs.pop("choices")
                else:
                    attrs.pop("choices", None)
            for key in ["name", "label", "field_type", "rules", "messages"]:
                attrs.pop(key, None)

            field = FormField(
                name, label, field_type, rules, messages, choices, **attrs
            )
            if is_post and not field.readonly:
                # Checkboxes and toggles need special handling: unchecked = not in form data
                if field_type in ("checkbox", "toggle"):
                    field.value = "1" if request.form.get(name) else "0"
                else:
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
                # Checkboxes and toggles need special handling: unchecked = not in form data
                if field.type in ("checkbox", "toggle"):
                    field.value = "1" if request.form.get(name) else "0"
                else:
                    field.value = request.form.get(name, "")
        return self

    def validate(
        self,
        request: Optional[Request] = None,
        csrf: bool = True,
        raise_error: bool = False,
    ) -> bool:
        """Run validation rules against the submitted request data.

        Returns True if all fields are valid, False otherwise.
        If the form is not yet bound to a request, it will attempt to bind using the provided request.

        Args:
            request: The request object to validate (if not already bound).
            csrf: If True, automatically performs CSRF verification.
            raise_error: If True, raises a ValidationError if validation fails.
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

        # 1. CSRF Verification
        if csrf:
            self._request.verify_csrf()

        schema = {}
        for name, field in self._fields.items():
            if not field.rules:
                continue
            if field.messages:
                schema[name] = (field.rules, field.messages)
            else:
                schema[name] = field.rules

        v = Validator(
            self._request.form, self._request.files, translate=self._request.__
        )
        result = v.rules(schema)

        # Update field-level errors
        for name, error in v.errors.items():
            if name in self._fields:
                self._fields[name]._error = error

        if not result and raise_error:
            raise ValidationError("Form validation failed", errors=v.errors)

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
                    val = source[name]
                    # Extract .value from Enum objects for form fields
                    if val is not None and isinstance(val, enum.Enum):
                        val = val.value
                    field.value = val if val is not None else ""
            else:
                if hasattr(source, name):
                    val = getattr(source, name)
                    # Extract .value from Enum objects for form fields
                    if val is not None and isinstance(val, enum.Enum):
                        val = val.value
                    field.value = val if val is not None else ""
        return self

    @property
    def errors(self):
        """Return a dictionary of validation errors for each field."""
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

            # Use field.label if defined, otherwise generate from field name
            label = field.label if field.label else name.replace("_", " ").title()

            # Use field.messages if defined for custom error messages
            messages = field.messages if field.messages else None

            # Build validation rules by combining auto-generated and custom rules
            rules_parts = []
            is_password = getattr(field, "is_password", False)
            if not field.nullable and not is_password:
                rules_parts.append("required")
            if getattr(field, "is_email", False):
                rules_parts.append("email")
            max_length = getattr(field, "max_length", None)
            if max_length:
                rules_parts.append(f"max:{max_length}")

            # Add custom rules from field definition
            if field.rules:
                rules_parts.append(field.rules)

            rules = "|".join(rules_parts)
            attrs = dict(getattr(field, "attrs", {}))
            if max_length:
                attrs["maxlength"] = max_length

            # Check if field has explicit form_type specified
            form_type = getattr(field, "form_type", None)
            if form_type:
                # Use the specified form type directly
                form_method = getattr(cls, form_type, None)
                if form_method and callable(form_method):
                    # Handle different form types with appropriate parameters
                    if form_type == "toggle":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "rating":
                        max_stars = attrs.pop("max_stars", 5)
                        schema[name] = form_method(
                            label, rules, messages, max_stars=max_stars, **attrs
                        )
                    elif form_type == "otp":
                        length = attrs.pop("length", 6)
                        schema[name] = form_method(
                            label, rules, messages, length=length, **attrs
                        )
                    elif form_type == "month":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "timerange":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "wysiwyg":
                        height = attrs.pop("height", 300)
                        schema[name] = form_method(
                            label, rules, messages, height=height, **attrs
                        )
                    elif form_type == "phone":
                        schema[name] = form_method(label, rules, messages, **attrs)
                    elif form_type == "autocomplete":
                        items = attrs.pop("items", [])
                        schema[name] = form_method(
                            label, items, rules, messages, **attrs
                        )
                    elif form_type == "signature":
                        width = attrs.pop("width", 400)
                        height = attrs.pop("height", 200)
                        schema[name] = form_method(
                            label, rules, messages, width=width, height=height, **attrs
                        )
                    else:
                        # Default: call with standard params
                        schema[name] = form_method(label, rules, messages, **attrs)
                    continue

            if is_password:
                schema[name] = cls.password(label, "", messages, **attrs)
            elif getattr(field, "is_file", False):
                schema[name] = cls.file(label, rules, messages, **attrs)
            elif getattr(field, "is_tel", False):
                rules = f"tel|{rules}".strip("|")
                schema[name] = cls.tel(label, rules, messages, **attrs)
            elif getattr(field, "is_url", False):
                rules = f"url|{rules}".strip("|")
                schema[name] = cls.url(label, rules, messages, **attrs)
            elif getattr(field, "is_color", False):
                rules = f"color|{rules}".strip("|")
                schema[name] = cls.color(label, rules, messages, **attrs)
            elif getattr(field, "is_time", False):
                schema[name] = cls.time(label, rules, messages, **attrs)
            elif getattr(field, "is_datetime", False):
                schema[name] = cls.datetime_local(label, rules, messages, **attrs)
            elif getattr(field, "is_enum", False):
                schema[name] = cls.enum(
                    label, field.enum_class, rules, messages, **attrs
                )
            elif getattr(field, "is_json", False):
                schema[name] = cls.json(label, rules, messages, **attrs)
            elif getattr(field, "is_decimal", False):
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, messages, **attrs)
            elif getattr(field, "is_uuid", False):
                attrs["readonly"] = True
                schema[name] = cls.text(label, rules, messages, **attrs)
            elif getattr(field, "is_foreign_key", False):
                target = field.related_model
                if getattr(field, "dropdown", False):
                    try:
                        items = target.all()
                    except Exception:
                        items = []
                    schema[name] = cls.dropdown(
                        label,
                        items,
                        title=getattr(field, "dropdown_title", "name"),
                        subtitle=getattr(field, "dropdown_subtitle", None),
                        image=getattr(field, "dropdown_image", None),
                        searchable=getattr(field, "dropdown_searchable", True),
                        rules=rules,
                        messages=messages,
                        **attrs,
                    )
                else:
                    try:
                        choices = [(o.id, str(o)) for o in target.all()]
                    except Exception:
                        choices = []
                    choices = [("", "— None —")] + choices
                    schema[name] = cls.select(label, choices, rules, messages, **attrs)
            elif getattr(field, "is_dropdown", False):
                schema[name] = cls.dropdown(
                    label,
                    [],
                    searchable=getattr(field, "dropdown_searchable", True),
                    choices=field.choices,
                    rules=rules,
                    messages=messages,
                    **attrs,
                )
            elif getattr(field, "is_boolean", False):
                schema[name] = cls.checkbox(label, "", messages, **attrs)
            elif field.sql_type == "INTEGER":
                # Treat INTEGER fields starting with "is_" or "has_" as checkboxes
                if name.startswith("is_") or name.startswith("has_"):
                    schema[name] = cls.checkbox(label, "", messages, **attrs)
                else:
                    schema[name] = cls.number(label, rules, messages, **attrs)
            elif field.sql_type == "REAL":
                precision = getattr(field, "precision", None)
                if precision is not None:
                    attrs["step"] = (
                        f"0.{'0' * (precision - 1)}1" if precision > 0 else "1"
                    )
                schema[name] = cls.number(label, rules, messages, **attrs)
            elif getattr(field, "is_email", False):
                schema[name] = cls.email(label, rules, messages, **attrs)
            elif getattr(field, "is_text", False):
                schema[name] = cls.textarea(label, rules, messages, **attrs)
            else:
                schema[name] = cls.text(label, rules, messages, **attrs)

        if not schema:
            raise ValueError(
                "Form.from_model({}) produced no fields (check include/exclude).".format(
                    getattr(model, "__name__", "model")
                )
            )

        return cls(schema, request)
