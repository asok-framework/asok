from __future__ import annotations

import enum
from typing import Any, Callable, Optional

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

    def _build_field_value(self, field_type: str, name: str, request: Optional[Request]) -> str:
        """Read a field value from a POST request, handling checkbox/toggle specially."""
        if field_type in ("checkbox", "toggle"):
            return "1" if request.form.get(name) else "0"
        return request.form.get(name, "")

    def _extract_choices_and_clean_attrs(self, definition: tuple) -> tuple[str, str, str, dict, list, dict]:
        field_type, label, rules, messages, choices, attrs = definition
        attrs = dict(attrs)
        if "choices" in attrs:
            if choices is None:
                choices = attrs.pop("choices")
            else:
                attrs.pop("choices", None)
        for key in ["name", "label", "field_type", "rules", "messages"]:
            attrs.pop(key, None)
        return field_type, label, rules, messages, choices, attrs

    def _build_single_field(self, name: str, definition: tuple, is_post: bool, request: Optional[Request]) -> FormField:
        field_type, label, rules, messages, choices, attrs = self._extract_choices_and_clean_attrs(definition)
        field = FormField(name, label, field_type, rules, messages, choices, **attrs)
        if is_post and not field.readonly:
            field.value = self._build_field_value(field_type, name, request)
        return field

    def _build_fields(self, fields_dict: dict, is_post: bool, request: Optional[Request]) -> dict[str, FormField]:
        """Create FormField objects from a field schema, populating values on POST."""
        fields = {}
        for name, definition in fields_dict.items():
            fields[name] = self._build_single_field(name, definition, is_post, request)
        return fields

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
        is_post = bool(request) and request.method == "POST"
        self._fields: dict[str, FormField] = self._build_fields(fields_dict, is_post, request)

    def _bind(self, request: Request) -> Form:
        """Internal helper for creating a bound copy of the form."""
        return Form(self._schema, request)

    def bind(self, request: Request) -> Form:
        """Attach a request to this form instance."""
        self._request = request
        self._is_template = False
        if request.method != "POST":
            return self
        for name, field in self._fields.items():
            if not field.readonly:
                field.value = self._build_field_value(field.type, name, request)
        return self

    def _build_validation_schema(self) -> dict:
        """Build a validation rules schema from the form fields."""
        schema = {}
        for name, field in self._fields.items():
            if not field.rules:
                continue
            schema[name] = (field.rules, field.messages) if field.messages else field.rules
        return schema

    def _apply_field_errors(self, errors: dict) -> None:
        """Apply validator errors to the corresponding form fields."""
        for name, error in errors.items():
            if name in self._fields:
                self._fields[name]._error = error

    def _ensure_bound_request(self, request: Optional[Request]) -> None:
        if request is not None and self._request is None:
            self.bind(request)
        if not self._request:
            raise RuntimeError(
                "Form.validate() requires a bound request. "
                "Ensure you accessed the form via request.shared() or called form.bind(request)."
            )

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
        self._ensure_bound_request(request)

        if self._request.method != "POST":
            return False

        if csrf:
            self._request.verify_csrf()

        schema = self._build_validation_schema()
        v = Validator(
            self._request.form, self._request.files, translate=self._request.__
        )
        result = v.rules(schema)
        self._apply_field_errors(v.errors)

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

    def _fill_field_value(self, field: FormField, val: Any) -> None:
        """Set a field's value from val, extracting Enum values if needed."""
        if val is not None and isinstance(val, enum.Enum):
            val = val.value
        field.value = val if val is not None else ""

    def _fill_single_field(self, name: str, field: FormField, source: Any, is_dict: bool) -> None:
        if is_dict:
            if name in source:
                self._fill_field_value(field, source[name])
        elif hasattr(source, name):
            self._fill_field_value(field, getattr(source, name))

    def fill(self, source: Any) -> Form:
        """Pre-fill field values from a model instance or a dictionary."""
        is_post = bool(self._request) and self._request.method == "POST"
        is_dict = isinstance(source, dict)
        for name, field in self._fields.items():
            if is_post and not field.readonly:
                continue
            self._fill_single_field(name, field, source, is_dict)
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
    def _skip_slug(cls, field: Any) -> bool:
        return bool(getattr(field, "is_slug", False) and getattr(field, "populate_from", None))

    @classmethod
    def _skip_hidden(cls, field: Any) -> bool:
        return bool(getattr(field, "hidden", False) and not getattr(field, "is_password", False))

    @classmethod
    def _skip_protected(cls, name: str, field: Any) -> bool:
        return bool(getattr(field, "protected", False) and name != "password")

    @classmethod
    def _should_skip_field(cls, name: str, field: Any) -> bool:
        if name == "id" or getattr(field, "is_timestamp", False):
            return True
        if getattr(field, "is_soft_delete", False) or getattr(field, "is_vector", False):
            return True
        return cls._skip_advanced(name, field)

    @classmethod
    def _skip_advanced(cls, name: str, field: Any) -> bool:
        if cls._skip_slug(field) or cls._skip_hidden(field):
            return True
        return cls._skip_protected(name, field)

    @classmethod
    def _build_field_attrs(cls, field: Any, max_length: Optional[int], rules_parts: list[str]) -> dict[str, Any]:
        if max_length:
            rules_parts.append(f"max:{max_length}")
        if field.rules:
            rules_parts.append(field.rules)
        attrs = dict(getattr(field, "attrs", {}))
        if max_length:
            attrs["maxlength"] = max_length
        return attrs

    @classmethod
    def _build_rules_and_attrs(cls, field: Any) -> tuple[str, dict[str, Any]]:
        rules_parts = []
        is_password = getattr(field, "is_password", False)
        if not field.nullable and not is_password:
            rules_parts.append("required")
        if getattr(field, "is_email", False):
            rules_parts.append("email")
        max_length = getattr(field, "max_length", None)
        attrs = cls._build_field_attrs(field, max_length, rules_parts)
        return "|".join(rules_parts), attrs

    @classmethod
    def _dispatch_custom_form_type_extended(
        cls,
        form_type: str,
        form_method: Callable,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if form_type == "wysiwyg":
            return form_method(label, rules, messages, height=attrs.pop("height", 300), **attrs)
        if form_type == "autocomplete":
            return form_method(label, attrs.pop("items", []), rules, messages, **attrs)
        if form_type == "signature":
            w = attrs.pop("width", 400)
            h = attrs.pop("height", 200)
            return form_method(label, rules, messages, width=w, height=h, **attrs)
        return form_method(label, rules, messages, **attrs)

    @classmethod
    def _dispatch_custom_form_type(
        cls,
        form_type: str,
        form_method: Callable,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if form_type in ("toggle", "month", "timerange", "phone"):
            return form_method(label, rules, messages, **attrs)
        if form_type == "rating":
            return form_method(label, rules, messages, max_stars=attrs.pop("max_stars", 5), **attrs)
        if form_type == "otp":
            return form_method(label, rules, messages, length=attrs.pop("length", 6), **attrs)
        return cls._dispatch_custom_form_type_extended(form_type, form_method, label, rules, messages, attrs)

    @classmethod
    def _create_form_field_by_type(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> Optional[FormField]:
        form_type = getattr(field, "form_type", None)
        if not form_type:
            return None
        form_method = getattr(cls, form_type, None)
        if not form_method or not callable(form_method):
            return None
        return cls._dispatch_custom_form_type(form_type, form_method, label, rules, messages, attrs)

    @classmethod
    def _apply_decimal_step(cls, field: Any, attrs: dict[str, Any]) -> None:
        prec = getattr(field, "precision", None)
        if prec is not None:
            attrs["step"] = f"0.{'0' * (prec - 1)}1" if prec > 0 else "1"

    @classmethod
    def _field_definition_fk(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        target = field.related_model
        if getattr(field, "dropdown", False):
            return cls.dropdown(
                label,
                lambda t=target: list(t.all()),
                title=getattr(field, "dropdown_title", "name"),
                subtitle=getattr(field, "dropdown_subtitle", None),
                image=getattr(field, "dropdown_image", None),
                searchable=getattr(field, "dropdown_searchable", True),
                rules=rules,
                messages=messages,
                **attrs,
            )
        return cls.select(
            label,
            lambda t=target: [("", "— None —")] + [(o.id, str(o)) for o in t.all()],
            rules,
            messages,
            **attrs
        )

    @classmethod
    def _field_definition_integer(
        cls,
        name: str,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if name.startswith("is_") or name.startswith("has_"):
            return cls.checkbox(label, "", messages, **attrs)
        return cls.number(label, rules, messages, **attrs)

    @classmethod
    def _field_definition_extended_6(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_email", False):
            return cls.email(label, rules, messages, **attrs)
        if getattr(field, "is_text", False):
            return cls.textarea(label, rules, messages, **attrs)
        return cls.text(label, rules, messages, **attrs)

    @classmethod
    def _field_definition_extended_5(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if field.sql_type == "INTEGER":
            return cls._field_definition_integer(name, label, rules, messages, attrs)
        if field.sql_type == "REAL":
            cls._apply_decimal_step(field, attrs)
            return cls.number(label, rules, messages, **attrs)
        return cls._field_definition_extended_6(name, field, label, rules, messages, attrs)

    @classmethod
    def _field_definition_extended_4(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_dropdown", False):
            return cls.dropdown(
                label,
                [],
                searchable=getattr(field, "dropdown_searchable", True),
                choices=field.choices,
                rules=rules,
                messages=messages,
                **attrs,
            )
        if getattr(field, "is_boolean", False):
            return cls.checkbox(label, "", messages, **attrs)
        return cls._field_definition_extended_5(name, field, label, rules, messages, attrs)

    @classmethod
    def _field_definition_extended_3(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_decimal", False):
            cls._apply_decimal_step(field, attrs)
            return cls.number(label, rules, messages, **attrs)
        if getattr(field, "is_uuid", False):
            attrs["readonly"] = True
            return cls.text(label, rules, messages, **attrs)
        if getattr(field, "is_foreign_key", False):
            return cls._field_definition_fk(name, field, label, rules, messages, attrs)
        return cls._field_definition_extended_4(name, field, label, rules, messages, attrs)

    @classmethod
    def _field_definition_extended_2(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_datetime", False):
            return cls.datetime_local(label, rules, messages, **attrs)
        if getattr(field, "is_enum", False):
            return cls.enum(label, field.enum_class, rules, messages, **attrs)
        if getattr(field, "is_json", False):
            return cls.json(label, rules, messages, **attrs)
        return cls._field_definition_extended_3(name, field, label, rules, messages, attrs)

    @classmethod
    def _field_definition_extended_1(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_url", False):
            return cls.url(label, f"url|{rules}".strip("|"), messages, **attrs)
        if getattr(field, "is_color", False):
            return cls.color(label, f"color|{rules}".strip("|"), messages, **attrs)
        if getattr(field, "is_time", False):
            return cls.time(label, rules, messages, **attrs)
        return cls._field_definition_extended_2(name, field, label, rules, messages, attrs)

    @classmethod
    def _field_definition_from_model_field(
        cls,
        name: str,
        field: Any,
        label: str,
        rules: str,
        messages: Optional[dict[str, str]],
        attrs: dict[str, Any]
    ) -> FormField:
        if getattr(field, "is_password", False):
            return cls.password(label, "", messages, **attrs)
        if getattr(field, "is_file", False):
            return cls.file(label, rules, messages, **attrs)
        if getattr(field, "is_tel", False):
            return cls.tel(label, f"tel|{rules}".strip("|"), messages, **attrs)
        return cls._field_definition_extended_1(name, field, label, rules, messages, attrs)

    @classmethod
    def _should_process_field(
        cls,
        name: str,
        field: Any,
        include: Optional[set[str]],
        exclude: set[str]
    ) -> bool:
        if include is not None and name not in include:
            return False
        if name in exclude:
            return False
        return not cls._should_skip_field(name, field)

    @classmethod
    def _build_field_schema_entry(
        cls,
        name: str,
        field: Any,
        include: Optional[set[str]],
        exclude: set[str],
        schema: dict[str, FormField]
    ) -> None:
        if not cls._should_process_field(name, field, include, exclude):
            return

        label = field.label if field.label else name.replace("_", " ").title()
        messages = field.messages if field.messages else None
        rules, attrs = cls._build_rules_and_attrs(field)

        custom_field = cls._create_form_field_by_type(name, field, label, rules, messages, attrs)
        if custom_field is not None:
            schema[name] = custom_field
        else:
            schema[name] = cls._field_definition_from_model_field(name, field, label, rules, messages, attrs)

    @classmethod
    def _validate_model_fields(cls, model: type[Model]) -> None:
        if not hasattr(model, "_fields"):
            raise TypeError(
                "Form.from_model() expected a Model class; got {}".format(
                    type(model).__name__
                )
            )

    @classmethod
    def _parse_include_exclude(
        cls,
        include_fields: Optional[list[str]],
        exclude_fields: Optional[list[str]]
    ) -> tuple[Optional[set[str]], set[str]]:
        include = set(include_fields) if include_fields else None
        exclude = set(exclude_fields) if exclude_fields else set()
        return include, exclude

    @classmethod
    def from_model(
        cls: type[Form],
        model: type[Model],
        request: Optional[Request] = None,
        include_fields: Optional[list[str]] = None,
        exclude_fields: Optional[list[str]] = None,
    ) -> Form:
        """Generate a Form instance automatically from a Model class."""
        cls._validate_model_fields(model)
        include, exclude = cls._parse_include_exclude(include_fields, exclude_fields)

        schema = {}
        for name, field in model._fields.items():
            cls._build_field_schema_entry(name, field, include, exclude, schema)

        if not schema:
            raise ValueError(
                "Form.from_model({}) produced no fields (check include/exclude).".format(
                    getattr(model, "__name__", "model")
                )
            )

        return cls(schema, request)
