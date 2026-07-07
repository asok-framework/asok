from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from .model import Model


class Field:
    """Definition of a database column with automatic form rendering and validation hints."""

    def __init__(
        self,
        sql_type: str,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        form_type: Optional[str] = None,
        **kwargs,
    ):
        self.sql_type: str = sql_type
        if isinstance(default, enum.Enum):
            default = default.value
        self.default: Any = default
        self.unique: bool = unique
        self.nullable: bool = nullable
        self.hidden: bool = hidden
        self.protected: bool = protected
        self.label: Optional[str] = label
        self.rules: Optional[str] = rules
        self.messages: dict[str, str] = messages or {}
        self.index: bool = index
        self.form_type: Optional[str] = form_type
        self.attrs: dict[str, Any] = kwargs

    @staticmethod
    def String(
        max_length: int = 255,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        form_type: Optional[str] = None,
        **kwargs,
    ) -> Field:
        """Short text, rendered as <input type="text">."""
        f = Field(
            "TEXT",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            index=index,
            form_type=form_type,
            **kwargs,
        )
        f.max_length = max_length
        return f

    @staticmethod
    def Text(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        wysiwyg: bool = False,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        **kwargs,
    ) -> Field:
        """Long text, rendered as <textarea>."""
        f = Field(
            "TEXT",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            index=index,
            **kwargs,
        )
        f.is_text = True
        f.wysiwyg = wysiwyg
        return f

    @staticmethod
    def SearchableText(
        max_length: Optional[int] = None,
        default: Any = None,
        nullable: bool = True,
        wysiwyg: bool = False,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Text field indexed for full-text search (FTS5)."""
        f = Field(
            "TEXT",
            default,
            False,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.searchable = True
        if max_length:
            f.max_length = max_length
        if wysiwyg or not max_length:
            f.is_text = True
        f.wysiwyg = wysiwyg
        return f

    @staticmethod
    def Email(
        max_length: int = 255,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Email field with automatic validation."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.max_length = max_length
        f.is_email = True
        return f

    @staticmethod
    def Tel(
        max_length: int = 20,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Telephone field with automatic validation."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.max_length = max_length
        f.is_tel = True
        return f

    @staticmethod
    def Integer(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        form_type: Optional[str] = None,
        **kwargs,
    ) -> Field:
        """Integer number (or rating if form_type='rating')."""
        return Field(
            "INTEGER",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            index=index,
            form_type=form_type,
            **kwargs,
        )

    @staticmethod
    def Boolean(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        form_type: Optional[str] = None,
        **kwargs,
    ) -> Field:
        """Boolean value, rendered as a checkbox (or toggle if form_type='toggle')."""
        f = Field(
            "INTEGER",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            index=index,
            form_type=form_type,
            **kwargs,
        )
        f.is_boolean = True
        return f

    @staticmethod
    def Float(
        precision: int = 2,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Floating-point number."""
        f = Field(
            "REAL",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.precision = precision
        return f

    @staticmethod
    def Date(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Date without time."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_date = True
        return f

    @staticmethod
    def DateTime(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        index: bool = False,
        **kwargs,
    ) -> Field:
        """Date and time."""
        f = Field(
            "TEXT",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            index=index,
            **kwargs,
        )
        f.is_datetime = True
        return f

    @staticmethod
    def Time(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Time only."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_time = True
        return f

    @staticmethod
    def ForeignKey(
        model_class: Union[str, type[Model]],
        default: Any = None,
        unique: bool = False,
        nullable: bool = False,
        autocomplete: bool = False,
        dropdown: bool = False,
        dropdown_title: str = "name",
        dropdown_subtitle: Optional[str] = None,
        dropdown_image: Optional[str] = None,
        dropdown_searchable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Relationship column pointing to another model."""
        f = Field(
            "INTEGER",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_foreign_key = True
        f.related_model = model_class
        f.autocomplete = autocomplete
        f.dropdown = dropdown
        f.dropdown_title = dropdown_title
        f.dropdown_subtitle = dropdown_subtitle
        f.dropdown_image = dropdown_image
        f.dropdown_searchable = dropdown_searchable
        return f

    @staticmethod
    def File(
        upload_to: str = "",
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Uploaded file reference."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_file = True
        f.upload_to = upload_to
        return f

    @staticmethod
    def Password(
        default: Any = None,
        unique: bool = False,
        nullable: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Hashed password field, hidden in forms and protected from mass assignment."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden=True,
            protected=True,
            label=label,
            rules=rules,
            messages=messages,
            **kwargs,
        )
        f.is_password = True
        return f

    @staticmethod
    def CreatedAt() -> Field:
        """Automatically populated timestamp on creation."""
        f = Field("TEXT", None, False, True)
        f.is_timestamp = True
        f.on = "create"
        return f

    @staticmethod
    def UpdatedAt() -> Field:
        """Automatically populated timestamp on every update."""
        f = Field("TEXT", None, False, True)
        f.is_timestamp = True
        f.on = "update"
        return f

    @staticmethod
    def SoftDelete() -> Field:
        """Column for logical deletions."""
        f = Field("TEXT", None, False, True)
        f.is_soft_delete = True
        return f

    @staticmethod
    def JSON(
        default: Any = None,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Field for storing JSON objects as text."""
        f = Field(
            "TEXT",
            default,
            False,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_json = True
        return f

    @staticmethod
    def Enum(
        enum_class: type[enum.Enum],
        default: Any = None,
        nullable: bool = False,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Restricted values from a Python Enum."""
        f = Field(
            "TEXT",
            default,
            False,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_enum = True
        f.enum_class = enum_class
        return f

    @staticmethod
    def Dropdown(
        choices: list[tuple[Any, str]],
        default: Any = None,
        nullable: bool = False,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        searchable: bool = True,
        **kwargs,
    ) -> Field:
        """Field for fixed-choice dropdowns with rich UI support."""
        f = Field(
            "TEXT",
            default,
            False,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_dropdown = True
        f.choices = choices
        f.dropdown_searchable = searchable
        return f

    @staticmethod
    def Decimal(
        precision: int = 2,
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Fixed-point decimal for currencies/accuracy."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_decimal = True
        f.precision = precision
        return f

    @staticmethod
    def UUID(
        default: Any = None,
        unique: bool = True,
        nullable: bool = False,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Universal unique identifier."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_uuid = True
        return f

    @staticmethod
    def Slug(
        populate_from: Optional[str] = None,
        unique: bool = True,
        nullable: bool = False,
        hidden: bool = False,
        protected: bool = False,
        always_update: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """URL-friendly string automatically generated from another field."""
        f = Field(
            "TEXT",
            None,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_slug = True
        f.populate_from = populate_from
        f.always_update = always_update
        return f

    @staticmethod
    def URL(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """URL string with validation."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_url = True
        return f

    @staticmethod
    def Color(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Hex color code, rendered as <input type="color">."""
        f = Field(
            "TEXT",
            default,
            unique,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_color = True
        return f

    @staticmethod
    def Vector(
        dimensions: int,
        default: Any = None,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Vector field for storing embeddings (BLOB)."""
        f = Field(
            "BLOB",
            default,
            False,
            nullable,
            hidden,
            protected,
            label,
            rules,
            messages,
            **kwargs,
        )
        f.is_vector = True
        f.dimensions = dimensions
        return f

    @staticmethod
    def EncryptedString(
        default: Any = None,
        unique: bool = False,
        nullable: bool = True,
        hidden: bool = False,
        protected: bool = False,
        label: Optional[str] = None,
        rules: Optional[str] = None,
        messages: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> Field:
        """Symmetrically encrypted text field storing data securely in the database.
        Uses application's SECRET_KEY for AES encryption.
        """
        f = Field(
            "TEXT",
            default=default,
            unique=unique,
            nullable=nullable,
            hidden=hidden,
            protected=protected,
            label=label,
            rules=rules,
            messages=messages,
            **kwargs,
        )
        f.is_encrypted = True
        return f
