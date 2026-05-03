from __future__ import annotations

import binascii
import datetime
import decimal
import enum
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import struct
import threading
import unicodedata
import uuid
import warnings
from typing import Any, Generic, Optional, TypeVar, Union

T = TypeVar("T", bound="Model")


class _Transaction:
    """Context manager for explicit database transactions."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn.execute("BEGIN")
        return self._conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._conn.rollback()
        else:
            self._conn.commit()
        return False


def _pluralize(word: str) -> str:
    """Basic English pluralization for table names."""
    if not word:
        return word
    word = word.lower()
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "sh", "ch", "x", "z")):
        return word + "es"
    return word + "s"


class ModelError(Exception):
    """Raised on ORM save/create errors with a user-friendly message."""

    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        original: Optional[Exception] = None,
    ):
        self.field: Optional[str] = field
        self.original: Optional[Exception] = original
        super().__init__(message)


_RE_UNIQUE = re.compile(r"UNIQUE constraint failed: \w+\.(\w+)")
_RE_NOT_NULL = re.compile(r"NOT NULL constraint failed: \w+\.(\w+)")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_TEL = re.compile(r"^\+?[\d\s\-\.]{7,20}$")


def _asok_cosine_similarity(v1, v2):
    """SQLite extension for cosine similarity: 1 - cosine_distance."""
    if not v1 or not v2:
        return 0.0
    try:
        # Standard lib way to convert bytes back to list of floats
        a = struct.unpack(f"{len(v1) // 4}f", v1)
        b = struct.unpack(f"{len(v2) // 4}f", v2)
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        n1 = math.sqrt(sum(x * x for x in a))
        n2 = math.sqrt(sum(x * x for x in b))
        if n1 == 0 or n2 == 0:
            return 0.0
        return dot / (n1 * n2)
    except Exception:
        return 0.0


def _asok_euclidean_distance(v1, v2):
    """SQLite extension for euclidean distance."""
    if not v1 or not v2:
        return 99999.0
    try:
        a = struct.unpack(f"{len(v1) // 4}f", v1)
        b = struct.unpack(f"{len(v2) // 4}f", v2)
        if len(a) != len(b):
            return 99999.0
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    except Exception:
        return 99999.0


class FileRef(str):
    """String subclass representing a file URL in the database.

    Automatically handles the '/uploads/' prefix mapping while preserving the raw filename.
    """

    def __new__(cls, name: str, upload_to: str = "") -> FileRef:
        if not name:
            instance = super().__new__(cls, "")
        elif upload_to:
            instance = super().__new__(cls, f"/uploads/{upload_to}/{name}")
        else:
            instance = super().__new__(cls, f"/uploads/{name}")
        instance.name = name
        return instance

    def __str__(self) -> str:
        s = super().__str__()
        if not s:
            return s
        if os.environ.get("IMAGE_OPTIMIZATION") == "true":
            if any(s.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                return s + ".webp"
        return s


class ModelList(list):
    """List subclass that supports .count() without arguments."""

    def count(self, value=None):
        if value is not None:
            return super().count(value)
        return len(self)


# Registry for all models to enable cross-model relationships
MODELS_REGISTRY = {}

# Thread-local storage for reusing SQLite connections
_local = threading.local()


_RE_SLUG_STRIP = re.compile(r"[^\w\s-]")
_RE_SLUG_SEP = re.compile(r"[-\s]+")


def slugify(text: Any) -> str:
    """Converts a string to a URL-friendly slug."""
    if not text:
        return ""
    text_str = str(text)
    text_norm = (
        unicodedata.normalize("NFKD", text_str)
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    text_clean = _RE_SLUG_STRIP.sub("", text_norm).strip().lower()
    return _RE_SLUG_SEP.sub("-", text_clean)


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
    ):
        self.sql_type: str = sql_type
        self.default: Any = default
        self.unique: bool = unique
        self.nullable: bool = nullable
        self.hidden: bool = hidden
        self.protected: bool = protected
        self.label: Optional[str] = label
        self.rules: Optional[str] = rules
        self.messages: dict[str, str] = messages or {}

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
    ) -> Field:
        """Short text, rendered as <input type="text">."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Long text, rendered as <textarea>."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Text field indexed for full-text search (FTS5)."""
        f = Field("TEXT", default, False, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Email field with automatic validation."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Telephone field with automatic validation."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Integer number."""
        return Field("INTEGER", default, unique, nullable, hidden, protected, label, rules, messages)

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
    ) -> Field:
        """Boolean value, rendered as a checkbox."""
        f = Field("INTEGER", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Floating-point number."""
        f = Field("REAL", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Date without time."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Date and time."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Time only."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Relationship column pointing to another model."""
        f = Field("INTEGER", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Uploaded file reference."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Hashed password field, hidden in forms and protected from mass assignment."""
        f = Field("TEXT", default, unique, nullable, hidden=True, protected=True, label=label, rules=rules, messages=messages)
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
    ) -> Field:
        """Field for storing JSON objects as text."""
        f = Field("TEXT", default, False, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Restricted values from a Python Enum."""
        f = Field("TEXT", default, False, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Field for fixed-choice dropdowns with rich UI support."""
        f = Field("TEXT", default, False, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Fixed-point decimal for currencies/accuracy."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Universal unique identifier."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """URL-friendly string automatically generated from another field."""
        f = Field("TEXT", None, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """URL string with validation."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Hex color code, rendered as <input type="color">."""
        f = Field("TEXT", default, unique, nullable, hidden, protected, label, rules, messages)
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
    ) -> Field:
        """Vector field for storing embeddings (BLOB)."""
        f = Field("BLOB", default, False, nullable, hidden, protected, label, rules, messages)
        f.is_vector = True
        f.dimensions = dimensions
        return f


class Relation:
    """Definition of a relationship between two models."""

    def __init__(
        self,
        type: str,
        target_model_name: str,
        foreign_key: Optional[str] = None,
        owner_key: str = "id",
        pivot_table: Optional[str] = None,
        pivot_fk: Optional[str] = None,
        pivot_other_fk: Optional[str] = None,
    ):
        self.type: str = type
        self.target_model_name: str = target_model_name
        self.foreign_key: Optional[str] = foreign_key
        self.owner_key: str = owner_key
        self.pivot_table: Optional[str] = pivot_table
        self.pivot_fk: Optional[str] = pivot_fk
        self.pivot_other_fk: Optional[str] = pivot_other_fk

    @staticmethod
    def HasMany(target_model_name: str, foreign_key: Optional[str] = None) -> Relation:
        """One-to-many relationship."""
        return Relation("HasMany", target_model_name, foreign_key)

    @staticmethod
    def HasOne(target_model_name: str, foreign_key: Optional[str] = None) -> Relation:
        """One-to-one relationship."""
        return Relation("HasOne", target_model_name, foreign_key)

    @staticmethod
    def BelongsTo(
        target_model_name: str, foreign_key: Optional[str] = None
    ) -> Relation:
        """Inverse of HasMany/HasOne relationship."""
        return Relation("BelongsTo", target_model_name, foreign_key)

    @staticmethod
    def BelongsToMany(
        target_model_name: str,
        pivot_table: Optional[str] = None,
        pivot_fk: Optional[str] = None,
        pivot_other_fk: Optional[str] = None,
    ) -> Relation:
        """Many-to-many relationship using a pivot table."""
        return Relation(
            "BelongsToMany",
            target_model_name,
            pivot_table=pivot_table,
            pivot_fk=pivot_fk,
            pivot_other_fk=pivot_other_fk,
        )


class Query(Generic[T]):
    """Chainable SQL query builder for a specific Model.

    Example:
        User.query().where("age", ">", 18).get()
    """

    _OPERATORS = {"=", "!=", "<", ">", "<=", ">=", "LIKE", "NOT LIKE", "IN", "NOT IN"}

    def __init__(self, model: type[T], with_trashed: bool = False):
        self.model: type[T] = model
        self._wheres: list[str] = []
        self._args: list[Any] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._eager: list[str] = []
        # Auto-filter soft-deleted rows unless explicitly included
        if model._soft_delete_field and not with_trashed:
            self._wheres.append(f"{model._soft_delete_field} IS NULL")

    def with_(self, *relation_names: str) -> Query[T]:
        """Eager load relationships to avoid N+1 query problems."""
        self._eager.extend(relation_names)
        return self

    def where(self, column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Add a where clause. (column, val) or (column, operator, val)."""
        if val is None:
            op, val = "=", op_or_val
        else:
            op = op_or_val.upper()
            if op not in self._OPERATORS:
                raise ValueError(f"Invalid operator: {op_or_val}")
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} {op} ?")
        self._args.append(val)
        return self

    def where_in(self, column: str, values: list[Any]) -> Query[T]:
        """Filter by a list of values."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        if not values:
            self._wheres.append("0")
            return self
        placeholders = ", ".join(["?"] * len(values))
        self._wheres.append(f"{column} IN ({placeholders})")
        self._args.extend(values)
        return self

    def like(self, column: str, pattern: str) -> Query[T]:
        """Filter using SQL LIKE operator."""
        return self.where(column, "LIKE", pattern)

    def or_where(self, column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Append an OR condition."""
        if val is None:
            op, val = "=", op_or_val
        else:
            op = op_or_val.upper()
            if op not in self._OPERATORS:
                raise ValueError(f"Invalid operator: {op_or_val}")

        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        if not self._wheres:
            return self.where(column, op, val)

        self._wheres.append(f"OR {column} {op} ?")
        self._args.append(val)
        return self

    def where_null(self, column: str) -> Query[T]:
        """Filter rows where column is NULL."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} IS NULL")
        return self



    def where_not_null(self, column: str) -> Query[T]:
        """Filter rows where column is NOT NULL."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} IS NOT NULL")
        return self

    def where_between(self, column: str, start: Any, end: Any) -> Query[T]:
        """Filter rows where column value is between start and end."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        self._wheres.append(f"{column} BETWEEN ? AND ?")
        self._args.extend([start, end])
        return self

    def nearest(
        self, column: str, vector: list[float], metric: str = "cosine", limit: int = 10
    ) -> Query[T]:
        """Perform a proximity search using vector similarity (cosine or euclidean)."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        # Serialize input vector to binary
        blob = struct.pack(f"{len(vector)}f", *vector)

        if metric == "cosine":
            self._order = f"cosine_similarity({column}, ?) DESC"
        else:
            self._order = f"euclidean_distance({column}, ?) ASC"

        self._args.append(blob)
        return self.limit(limit)

    def order_by(self, column: str) -> Query[T]:
        """Sort the query results. Use '-column' for descending order."""
        col = column.lstrip("-")
        if not self.model._valid_column(col):
            raise ValueError(f"Invalid column: {col}")
        direction = "DESC" if column.startswith("-") else "ASC"
        self._order = f"{col} {direction}"
        return self

    def latest(self, column: str = "created_at") -> Query[T]:
        """Order by the given column descending (default: created_at)."""
        if not self.model._valid_column(column):
            column = "id"
        return self.order_by(f"-{column}")

    def oldest(self, column: str = "created_at") -> Query[T]:
        """Order by the given column ascending (default: created_at)."""
        if not self.model._valid_column(column):
            column = "id"
        return self.order_by(column)

    def limit(self, n: int) -> Query[T]:
        """Limit the number of records returned."""
        self._limit = int(n)
        return self

    def offset(self, n: int) -> Query[T]:
        """Skip the first N records."""
        self._offset = int(n)
        return self

    def _build_where(self) -> str:
        """Build the WHERE clause, correctly handling OR fragments."""
        if not self._wheres:
            return ""
        where_sql = ""
        for i, w in enumerate(self._wheres):
            if i == 0:
                where_sql += w
            elif w.startswith("OR "):
                where_sql += " " + w
            else:
                where_sql += " AND " + w
        return " WHERE " + where_sql

    def _build(self, select: str = "*") -> str:
        """Internal helper to construct the SQL query string."""
        sql = f"SELECT {select} FROM {self.model._table}"
        sql += self._build_where()
        if self._order:
            sql += f" ORDER BY {self._order}"
        if self._limit is not None:
            sql += f" LIMIT {self._limit}"
        if self._offset is not None:
            sql += f" OFFSET {self._offset}"
        return sql

    def get(self) -> ModelList[T]:
        """Execute the query and return a ModelList of results."""
        sql = self._build()
        with self.model._get_conn() as conn:
            rows = conn.execute(sql, self._args).fetchall()
        results = ModelList(self.model(_trust=True, **dict(row)) for row in rows)
        if self._eager and results:
            self._load_eager(results)
        return results

    def _load_eager(self, results):
        """Batch load relations to avoid N+1 queries."""
        for rel_name in self._eager:
            rel = self.model._relations.get(rel_name)
            if not rel:
                continue
            target = MODELS_REGISTRY.get(rel.target_model_name)
            if not target:
                continue

            if rel.type in ("HasMany", "HasOne"):
                fk = rel.foreign_key or f"{self.model.__name__.lower()}_id"
                ids = [r.id for r in results if r.id]
                if not ids:
                    continue
                children = Query(target).where_in(fk, ids).get()
                grouped = {}
                for c in children:
                    grouped.setdefault(getattr(c, fk), []).append(c)
                for r in results:
                    items = grouped.get(r.id, [])
                    if rel.type == "HasMany":
                        r.__dict__[f"_eager_{rel_name}"] = items
                    else:
                        r.__dict__[f"_eager_{rel_name}"] = items[0] if items else None

            elif rel.type == "BelongsTo":
                fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
                parent_ids = list({getattr(r, fk) for r in results if getattr(r, fk)})
                if not parent_ids:
                    continue
                parents = Query(target).where_in("id", parent_ids).get()
                by_id = {p.id: p for p in parents}
                for r in results:
                    r.__dict__[f"_eager_{rel_name}"] = by_id.get(getattr(r, fk))

    def first(self) -> Optional[T]:
        """Execute the query and return the first matching record or None."""
        self._limit = 1
        rows = self.get()
        return rows[0] if rows else None

    def count(self) -> int:
        """Return the number of records matching the query."""
        sql = self._build(select="COUNT(*)")
        with self.model._get_conn() as conn:
            return conn.execute(sql, self._args).fetchone()[0]

    def _aggregate(self, func: str, column: str) -> Any:
        """Perform a SQL aggregate function (SUM, AVG, etc.) on a column."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        sql = self._build(select=f"{func}({column})")
        with self.model._get_conn() as conn:
            result = conn.execute(sql, self._args).fetchone()[0]
        return result if result is not None else 0

    def sum(self, column: str) -> Union[int, float]:
        """Calculate the sum of a numeric column."""
        return self._aggregate("SUM", column)

    def avg(self, column: str) -> float:
        """Calculate the average of a numeric column."""
        return self._aggregate("AVG", column)

    def min(self, column: str) -> Any:
        """Find the minimum value of a column."""
        return self._aggregate("MIN", column)

    def max(self, column: str) -> Any:
        """Find the maximum value of a column."""
        return self._aggregate("MAX", column)

    def pluck(self, column: str) -> list[Any]:
        """Return a flat list of values for a single column across all matches."""
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        sql = self._build(select=column)
        with self.model._get_conn() as conn:
            rows = conn.execute(sql, self._args).fetchall()
        return [row[0] for row in rows]

    def update(self, **values: Any) -> int:
        """Bulk update matching rows with the provided values."""
        if not values:
            return 0
        for col in values:
            if not self.model._valid_column(col):
                raise ValueError(f"Invalid column: {col}")
        set_str = ", ".join([f"{k} = ?" for k in values])
        sql = f"UPDATE {self.model._table} SET {set_str}"
        args = list(values.values())
        sql += self._build_where()
        args += self._args
        with self.model._get_conn() as conn:
            cursor = conn.execute(sql, args)
            return cursor.rowcount

    def exists(self) -> bool:
        """Return True if any records match the query."""
        return self.count() > 0

    def delete(self) -> int:
        """Bulk delete matching records (handles soft delete if enabled)."""
        if self.model._soft_delete_field:
            return self.update(
                **{self.model._soft_delete_field: datetime.datetime.now().isoformat()}
            )
        sql = f"DELETE FROM {self.model._table}"
        sql += self._build_where()
        with self.model._get_conn() as conn:
            cursor = conn.execute(sql, self._args)
            return cursor.rowcount

    def force_delete(self) -> int:
        """Bulk delete matching records permanently, bypassing soft delete."""
        sql = f"DELETE FROM {self.model._table}"
        sql += self._build_where()
        with self.model._get_conn() as conn:
            cursor = conn.execute(sql, self._args)
            return cursor.rowcount

    def paginate(self, page: int = 1, per_page: int = 10) -> dict[str, Any]:
        """Paginate the current query and return results with metadata.

        Example:
            User.query().where("active", 1).paginate(page=2)
        """
        total = self.count()
        pages = math.ceil(total / per_page)
        items = self.limit(per_page).offset((page - 1) * per_page).get()

        return {
            "items": items,
            "total": total,
            "pages": pages,
            "current_page": page,
        }


class ModelMeta(type):
    def __new__(mcs, name, bases, attrs):
        if name == "Model":
            return super().__new__(mcs, name, bases, attrs)

        fields = {k: v for k, v in attrs.items() if isinstance(v, Field)}
        attrs["_fields"] = fields
        attrs["_fields_list"] = list(fields.keys())
        attrs["_password_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_password")
        ]
        attrs["_slug_fields"] = [k for k, v in fields.items() if hasattr(v, "is_slug")]
        attrs["_timestamp_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_timestamp")
        ]
        attrs["_file_fields"] = [k for k, v in fields.items() if hasattr(v, "is_file")]
        attrs["_email_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_email")
        ]
        attrs["_tel_fields"] = [k for k, v in fields.items() if hasattr(v, "is_tel")]
        attrs["_json_fields"] = [k for k, v in fields.items() if hasattr(v, "is_json")]
        attrs["_decimal_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_decimal")
        ]
        attrs["_enum_fields"] = [k for k, v in fields.items() if hasattr(v, "is_enum")]
        attrs["_uuid_fields"] = [k for k, v in fields.items() if hasattr(v, "is_uuid")]
        attrs["_vector_fields"] = [
            k for k, v in fields.items() if hasattr(v, "is_vector")
        ]
        soft_delete_fields = [
            k for k, v in fields.items() if hasattr(v, "is_soft_delete")
        ]
        attrs["_soft_delete_field"] = (
            soft_delete_fields[0] if soft_delete_fields else None
        )
        attrs["_search_fields"] = [
            k for k, v in fields.items() if getattr(v, "searchable", False)
        ]
        # Use explicit __tablename__ if provided, otherwise auto-pluralize
        attrs["_table"] = attrs.get("__tablename__", _pluralize(name))
        attrs["_model_name"] = name
        attrs["_conn_attr"] = f"conn_{attrs.get('_db_path', 'db.sqlite3')}"

        relations = {k: v for k, v in attrs.items() if isinstance(v, Relation)}
        attrs["_relations"] = relations

        for k, v in fields.items():
            if hasattr(v, "is_foreign_key"):
                rel_name = k.replace("_id", "")

                def get_related(self, field_name=k, model=v.related_model):
                    val = getattr(self, field_name)
                    return model.find(id=val) if val else None

                attrs[rel_name] = property(get_related)

        for k, v in relations.items():
            if v.type == "HasMany":

                def get_collection(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return []
                    fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
                    return target_model.all(**{fk: self.id})

                attrs[k] = property(get_collection)

            elif v.type == "HasOne":

                def get_one(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return None
                    fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
                    return target_model.find(**{fk: self.id})

                attrs[k] = property(get_one)

            elif v.type == "BelongsTo":

                def get_parent(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return None
                    fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
                    val = getattr(self, fk, None)
                    return target_model.find(id=val) if val else None

                attrs[k] = property(get_parent)

            elif v.type == "BelongsToMany":

                def get_many_to_many(self, rel=v, rel_name=k):
                    cached = self.__dict__.get(f"_eager_{rel_name}")
                    if cached is not None:
                        return cached
                    target_model = MODELS_REGISTRY.get(rel.target_model_name)
                    if not target_model:
                        return []
                    pivot, pfk, pofk = self._pivot_info(rel)
                    sql = (
                        f"SELECT t.* FROM {target_model._table} t "
                        f"JOIN {pivot} p ON p.{pofk} = t.id "
                        f"WHERE p.{pfk} = ?"
                    )
                    with self._get_conn() as conn:
                        rows = conn.execute(sql, (self.id,)).fetchall()
                    return ModelList(target_model(**dict(row)) for row in rows)

                attrs[k] = property(get_many_to_many)

        for k in fields:
            if k in attrs and isinstance(attrs[k], Field):
                attrs.pop(k)

        cls = super().__new__(mcs, name, bases, attrs)
        if name != "Model":
            MODELS_REGISTRY[name] = cls
        return cls


class Model(metaclass=ModelMeta):
    """Base class for all ORM models."""

    _db_path: str = "db.sqlite3"

    def __init__(self, _trust: bool = False, **kwargs: Any):
        self.id: Optional[int] = kwargs.get("id")
        is_new = not self.id  # New instance being created
        for name in self._fields:
            field = self._fields[name]
            if name in kwargs:
                # Security: prevent mass assignment for protected fields unless trusted
                # Exception: allow password fields during creation (new instances)
                if not _trust and getattr(field, "protected", False):
                    # Allow password assignment during creation, block during updates
                    if getattr(field, "is_password", False) and is_new:
                        val = kwargs[name]
                    else:
                        val = field.default
                else:
                    val = kwargs[name]
            else:
                val = getattr(self, name, field.default)

            if val is not None:
                if hasattr(field, "is_file") and not isinstance(val, FileRef):
                    val = FileRef(val, field.upload_to)
                elif hasattr(field, "is_boolean"):
                    # Convert string/bool to int (0 or 1)
                    if isinstance(val, str):
                        val = 1 if val and val != "0" else 0
                    elif isinstance(val, bool):
                        val = 1 if val else 0
                    elif val:
                        val = int(bool(val))
                elif hasattr(field, "is_json") and isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except Exception:
                        pass
                elif hasattr(field, "is_decimal") and not isinstance(
                    val, decimal.Decimal
                ):
                    try:
                        val = decimal.Decimal(str(val))
                    except Exception:
                        pass
                elif hasattr(field, "is_enum") and not isinstance(val, enum.Enum):
                    try:
                        val = field.enum_class(val)
                    except Exception:
                        pass
                elif hasattr(field, "is_vector") and isinstance(
                    val, (bytes, bytearray)
                ):
                    try:
                        val = list(struct.unpack(f"{len(val) // 4}f", val))
                    except Exception:
                        val = []

                # Automatic SafeString for WYSIWYG content
                if getattr(field, "wysiwyg", False) and isinstance(val, str):
                    from .templates import SafeString

                    val = SafeString(val)

            setattr(self, name, val)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id}>"

    def to_dict(self) -> dict[str, Any]:
        """Convert the model instance and its fields to a dictionary.

        Automatically excludes hidden and password fields for security.
        """
        data = {"id": self.id}
        for name in self._fields:
            field = self._fields[name]
            if getattr(field, "hidden", False) or getattr(field, "is_password", False):
                continue
            val = getattr(self, name)
            if isinstance(val, (datetime.date, datetime.datetime)):
                data[name] = val.isoformat()
            else:
                data[name] = val
        return data

    def _hash_value(self, password):
        salt = secrets.token_hex(16)
        iterations = 200000
        hash_bytes = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
        )
        return f"pbkdf2:sha256:{iterations}${salt}${binascii.hexlify(hash_bytes).decode('utf-8')}"

    def check_password(self, field_name: str, password: str) -> bool:
        """Verify a plain-text password against a hashed field value."""
        hashed = getattr(self, field_name, None)
        if not hashed or not str(hashed).startswith("pbkdf2:"):
            return False

        try:
            method_info, salt, stored_hash = hashed.split("$")
            _, _, iterations = method_info.split(":")
            num_iterations = int(iterations)
            test_hash = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt.encode("utf-8"), num_iterations
            )
            return hmac.compare_digest(
                binascii.hexlify(test_hash).decode("utf-8"), stored_hash
            )
        except Exception:
            return False

    @classmethod
    def _get_conn(cls):
        attr = cls._conn_attr
        conn = getattr(_local, attr, None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(cls._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        # ASOK VECTOR EXTENSION
        conn.create_function("cosine_similarity", 2, _asok_cosine_similarity)
        conn.create_function("euclidean_distance", 2, _asok_euclidean_distance)

        setattr(_local, attr, conn)
        # Track for cleanup
        if not hasattr(_local, "_all_conns"):
            _local._all_conns = []
        _local._all_conns.append(conn)
        return conn

    @classmethod
    def close_connections(cls):
        """Close all SQLite connections held by the current thread."""
        for conn in getattr(_local, "_all_conns", []):
            try:
                conn.close()
            except Exception:
                pass
        _local._all_conns = []
        for attr in list(vars(_local)):
            if attr.startswith("conn_"):
                delattr(_local, attr)

    @classmethod
    def create_table(cls):
        f_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for name, f in cls._fields.items():
            def_str = f"{name} {f.sql_type}"
            if f.unique:
                def_str += " UNIQUE"
            if not f.nullable:
                def_str += " NOT NULL"
            if f.default is not None:
                if isinstance(f.default, bool):
                    d = str(f.default).lower()
                elif isinstance(f.default, (int, float)):
                    d = str(f.default)
                else:
                    d = "'" + str(f.default).replace("'", "''") + "'"
                def_str += f" DEFAULT {d}"
            f_defs.append(def_str)

        sql = f"CREATE TABLE IF NOT EXISTS {cls._table} ({', '.join(f_defs)})"
        with cls._get_conn() as conn:
            conn.execute(sql)
            if cls._search_fields:
                # Create FTS5 virtual table
                f_names = ", ".join(cls._search_fields)
                fts_sql = f"CREATE VIRTUAL TABLE IF NOT EXISTS {cls._table}_fts USING fts5({f_names}, content='{cls._table}', content_rowid='id')"
                conn.execute(fts_sql)

                # Triggers to keep FTS in sync
                ai = f"""CREATE TRIGGER IF NOT EXISTS {cls._table}_ai AFTER INSERT ON {cls._table} BEGIN
                    INSERT INTO {cls._table}_fts(rowid, {f_names}) VALUES (new.id, {", ".join([f"new.{n}" for n in cls._search_fields])});
                END;"""
                ad = f"""CREATE TRIGGER IF NOT EXISTS {cls._table}_ad AFTER DELETE ON {cls._table} BEGIN
                    INSERT INTO {cls._table}_fts({cls._table}_fts, rowid, {f_names}) VALUES('delete', old.id, {", ".join([f"old.{n}" for n in cls._search_fields])});
                END;"""
                au = f"""CREATE TRIGGER IF NOT EXISTS {cls._table}_au AFTER UPDATE ON {cls._table} BEGIN
                    INSERT INTO {cls._table}_fts({cls._table}_fts, rowid, {f_names}) VALUES('delete', old.id, {", ".join([f"old.{n}" for n in cls._search_fields])});
                    INSERT INTO {cls._table}_fts(rowid, {f_names}) VALUES (new.id, {", ".join([f"new.{n}" for n in cls._search_fields])});
                END;"""
                conn.execute(ai)
                conn.execute(ad)
                conn.execute(au)

            # Create pivot tables for BelongsToMany relationships
            if hasattr(cls, "_relations"):
                for rel_name, rel in cls._relations.items():
                    if rel.type == "BelongsToMany":
                        # Compute pivot table name and foreign keys
                        a = cls.__name__.lower()
                        b = rel.target_model_name.lower()
                        pivot_table = rel.pivot_table or "_".join(sorted([a, b]))
                        pivot_fk = rel.pivot_fk or f"{a}_id"
                        pivot_other_fk = rel.pivot_other_fk or f"{b}_id"

                        # Create the pivot table
                        pivot_sql = f"""
                        CREATE TABLE IF NOT EXISTS {pivot_table} (
                            {pivot_fk} INTEGER NOT NULL,
                            {pivot_other_fk} INTEGER NOT NULL,
                            PRIMARY KEY ({pivot_fk}, {pivot_other_fk}),
                            FOREIGN KEY ({pivot_fk}) REFERENCES {cls._table}(id) ON DELETE CASCADE,
                            FOREIGN KEY ({pivot_other_fk}) REFERENCES {_pluralize(b)}(id) ON DELETE CASCADE
                        )
                        """
                        conn.execute(pivot_sql)

    @classmethod
    def create(cls: type[T], _trust: bool = False, **kwargs: Any) -> T:
        """Create a new model instance, save it to the database, and return it."""
        obj = cls(_trust=_trust, **kwargs)
        obj.save()
        return obj

    # ── Model event hooks (override in subclasses) ───────────
    def before_save(self):
        pass

    def after_save(self):
        pass

    def before_create(self):
        pass

    def after_create(self):
        pass

    def before_update(self):
        pass

    def after_update(self):
        pass

    def before_delete(self):
        pass

    def after_delete(self):
        pass

    def update(self, **values: Any) -> Model:
        """Set multiple attributes and save the model in one call.

        Security: prevents mass assignment of protected fields.
        """
        for k, v in values.items():
            field = self._fields.get(k)
            if field and field.protected:
                continue
            setattr(self, k, v)
        self.save()
        return self

    def increment(self, column: str, amount: int = 1) -> Model:
        """Atomically increment a numeric column in the database."""
        if not self.id:
            raise ModelError("Cannot increment unsaved model")
        if not self._valid_column(column):
            raise ValueError(f"Invalid column: {column}")
        sql = f"UPDATE {self._table} SET {column} = {column} + ? WHERE id = ?"
        with self._get_conn() as conn:
            conn.execute(sql, (amount, self.id))
        return self.refresh()

    def decrement(self, column, amount=1):
        """Atomic decrement of a column."""
        return self.increment(column, -amount)

    def refresh(self) -> Model:
        """Reload all attributes from the latest database state."""
        if not self.id:
            return self
        fresh = self.__class__.find(id=self.id)
        if fresh:
            self.__dict__.update(fresh.__dict__)
        return self

    @classmethod
    def find_or_fail(cls: type[T], id: Optional[Any] = None, **kwargs: Any) -> T:
        """Find a single record matching kwargs, or raise ModelError if not found."""
        if id is not None:
            kwargs["id"] = id
        obj = cls.find(**kwargs)
        if not obj:
            raise ModelError(f"{cls._model_name} not found", field="id")
        return obj

    def save(self) -> None:
        """Persist the model instance to the database (INSERT or UPDATE)."""
        is_new = not self.id
        self.before_save()
        if is_new:
            self.before_create()
        else:
            self.before_update()

        for name in self._email_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_EMAIL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid email address.",
                    field=name,
                )

        for name in self._tel_fields:
            val = getattr(self, name, None)
            if val in (None, ""):
                continue
            if not _RE_TEL.match(str(val)):
                raise ModelError(
                    f"{name.replace('_', ' ').capitalize()} is not a valid phone number.",
                    field=name,
                )

        for name in self._password_fields:
            val = getattr(self, name)
            if val and not str(val).startswith("pbkdf2:"):
                setattr(self, name, self._hash_value(str(val)))

        for name in self._uuid_fields:
            if not getattr(self, name):
                setattr(self, name, str(uuid.uuid4()))

        for name in self._slug_fields:
            field = self._fields[name]
            populate = getattr(field, "populate_from", None)
            always_update = getattr(field, "always_update", False)
            if populate and (not getattr(self, name) or always_update):
                source_val = getattr(self, populate, None)
                if source_val:
                    setattr(self, name, slugify(source_val))

        if self._timestamp_fields:
            now = datetime.datetime.now().isoformat()
            for name in self._timestamp_fields:
                field = self._fields[name]
                if field.on == "create" and not self.id and not getattr(self, name):
                    setattr(self, name, now)
                elif field.on == "update":
                    setattr(self, name, now)

        fields = self._fields_list
        values = []
        for f in fields:
            field = self._fields[f]
            val = getattr(self, f)
            if val is None:
                values.append(None)
            elif isinstance(val, FileRef):
                values.append(val.name)
            elif hasattr(field, "is_json"):
                values.append(json.dumps(val))
            elif hasattr(field, "is_decimal"):
                values.append(str(val))
            elif hasattr(field, "is_enum"):
                # Handle both Enum objects and raw strings
                if isinstance(val, enum.Enum):
                    values.append(val.value)
                else:
                    values.append(val)
            elif hasattr(field, "is_vector"):
                if val is None:
                    values.append(None)
                else:
                    # Validate dimensions
                    if len(val) != field.dimensions:
                        raise ModelError(
                            f"Vector field '{f}' expects {field.dimensions} dims, got {len(val)}"
                        )
                    values.append(struct.pack(f"{len(val)}f", *val))
            else:
                values.append(val)

        if self.id:
            set_str = ", ".join([f"{f} = ?" for f in fields])
            sql = f"UPDATE {self._table} SET {set_str} WHERE id = ?"
            args = values + [self.id]
        else:
            placeholders = ", ".join(["?" for _ in fields])
            sql = f"INSERT INTO {self._table} ({', '.join(fields)}) VALUES ({placeholders})"
            args = values

        with self._get_conn() as conn:
            try:
                cursor = conn.execute(sql, args)
                conn.commit()
            except sqlite3.IntegrityError as e:
                conn.rollback()
                msg = str(e)
                m = _RE_UNIQUE.search(msg)
                if m:
                    field = m.group(1)
                    raise ModelError(
                        f"{field} already exists", field=field, original=e
                    ) from None
                m = _RE_NOT_NULL.search(msg)
                if m:
                    field = m.group(1)
                    raise ModelError(
                        f"{field} is required", field=field, original=e
                    ) from None
                raise ModelError(msg, original=e) from None
            if not self.id:
                self.id = cursor.lastrowid

        if is_new:
            self.after_create()
        else:
            self.after_update()
        self.after_save()

    @classmethod
    def transaction(cls):
        """Context manager for database transactions.

        Usage:
            with User.transaction():
                user.save()
                profile.save()
        """
        return _Transaction(cls._get_conn())

    @classmethod
    def _valid_column(cls, col):
        return col == "id" or col in cls._fields

    @classmethod
    def query(cls: type[T], with_trashed: bool = False) -> Query[T]:
        """Start a new chainable query for this model."""
        return Query(cls, with_trashed=with_trashed)

    @classmethod
    def where(cls: type[T], column: str, op_or_val: Any, val: Any = None) -> Query[T]:
        """Start a new query with an initial where clause."""
        return Query(cls).where(column, op_or_val, val)

    @classmethod
    def where_in(cls: type[T], column: str, values: list[Any]) -> Query[T]:
        """Start a new query with an initial where_in clause."""
        return Query(cls).where_in(column, values)

    @classmethod
    def _soft_delete_where(cls):
        if cls._soft_delete_field:
            return f"{cls._soft_delete_field} IS NULL"
        return None

    @classmethod
    def all(
        cls: type[T],
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> ModelList[T]:
        """Fetch all records matching simple field criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        if wheres:
            sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)}"
        else:
            sql = f"SELECT * FROM {cls._table}"

        if order_by:
            col = order_by.lstrip("-")
            if not cls._valid_column(col):
                raise ValueError(f"Invalid column for order_by: {col}")
            direction = "DESC" if order_by.startswith("-") else "ASC"
            sql += f" ORDER BY {col} {direction}"

        if limit:
            sql += " LIMIT ?"
            args.append(limit)

        with cls._get_conn() as conn:
            rows = conn.execute(sql, args).fetchall()
            return ModelList(cls(**dict(row)) for row in rows)

    @classmethod
    def count(cls, **kwargs):
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        if wheres:
            sql = f"SELECT COUNT(*) FROM {cls._table} WHERE {' AND '.join(wheres)}"
        else:
            sql = f"SELECT COUNT(*) FROM {cls._table}"
        with cls._get_conn() as conn:
            return conn.execute(sql, args).fetchone()[0]

    @classmethod
    def exists(cls, **kwargs):
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT 1 FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        with cls._get_conn() as conn:
            return conn.execute(sql, args).fetchone() is not None

    @classmethod
    def search(
        cls: type[T], term: str, limit: int = 10, offset: int = 0
    ) -> ModelList[T]:
        """Perform a full-text search against indexed fields."""
        if not cls._search_fields:
            return ModelList()

        sd_where = ""
        if cls._soft_delete_field:
            sd_where = f"AND t.{cls._soft_delete_field} IS NULL"

        sql = f"""
            SELECT t.* FROM {cls._table} t
            JOIN {cls._table}_fts f ON t.id = f.rowid
            WHERE f.{cls._table}_fts MATCH ? {sd_where}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """
        with cls._get_conn() as conn:
            rows = conn.execute(sql, (term, limit, offset)).fetchall()
        return ModelList(cls(**dict(row)) for row in rows)

    @classmethod
    def first_or_create(cls, defaults=None, **kwargs):
        """Find a row matching kwargs, or create one with kwargs+defaults."""
        obj = cls.find(**kwargs)
        if obj:
            return obj
        data = dict(kwargs)
        if defaults:
            data.update(defaults)
        return cls.create(**data)

    @classmethod
    def update_or_create(cls, defaults=None, **kwargs):
        """Find + update, or create. defaults are the values to set."""
        obj = cls.find(**kwargs)
        if obj:
            if defaults:
                for k, v in defaults.items():
                    setattr(obj, k, v)
                obj.save()
            return obj
        data = dict(kwargs)
        if defaults:
            data.update(defaults)
        return cls.create(**data)

    @classmethod
    def raw(cls, sql, args=None):
        """Execute raw SQL and return a ModelList of instances.

        Column names in the result must match model field names.

        Security: Always use parameterised queries with ``?`` placeholders
        and pass user-supplied values via the ``args`` list.  **Never**
        interpolate user input directly into the ``sql`` string — doing so
        opens the door to SQL injection.

        Good:  ``User.raw("SELECT * FROM users WHERE email = ?", [email])``
        Bad:   ``User.raw(f"SELECT * FROM users WHERE email = '{email}'")``
        """
        # SECURITY: Warn if SQL contains suspicious patterns that might indicate
        # direct user input interpolation instead of parameterized queries

        # Check for common SQL injection patterns
        suspicious_patterns = [
            r"=\s*['\"].*?['\"]",  # = 'value' or = "value"
            r"(?:WHERE|AND|OR)\s+.*?=\s*f['\"]",  # f-string interpolation
            r"\{.*?\}",  # Python f-string placeholders
            r"%\(.*?\)",  # Python % formatting
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, sql, re.IGNORECASE):
                warnings.warn(
                    f"SECURITY WARNING: Raw SQL query may contain interpolated values. "
                    f"Use parameterized queries with '?' placeholders and pass values via args parameter. "
                    f"Query: {sql[:100]}...",
                    UserWarning,
                    stacklevel=2
                )
                break

        with cls._get_conn() as conn:
            rows = conn.execute(sql, args or []).fetchall()
        return ModelList(cls(**dict(row)) for row in rows)

    @classmethod
    def find(cls: type[T], **kwargs: Any) -> Optional[T]:
        """Find the first record matching simple field criteria."""
        for k in kwargs:
            if not cls._valid_column(k):
                raise ValueError(f"Invalid column: {k}")
        wheres = [f"{k} = ?" for k in kwargs]
        args = list(kwargs.values())
        sd = cls._soft_delete_where()
        if sd:
            wheres.append(sd)
        sql = f"SELECT * FROM {cls._table} WHERE {' AND '.join(wheres)} LIMIT 1"
        with cls._get_conn() as conn:
            row = conn.execute(sql, args).fetchone()
            return cls(_trust=True, **dict(row)) if row else None

    @classmethod
    def destroy(cls, **kwargs: Any) -> int:
        """Delete records matching criteria (handles soft delete if enabled)."""
        q = cls.query()
        for k, v in kwargs.items():
            q.where(k, v)

        if cls._soft_delete_field:
            now = datetime.datetime.now().isoformat()
            return q.update(**{cls._soft_delete_field: now})
        return q.delete()

    @classmethod
    def force_destroy(cls, **kwargs: Any) -> int:
        """Permanently delete records matching criteria, bypassing soft delete."""
        q = cls.query(with_trashed=True)
        for k, v in kwargs.items():
            q.where(k, v)
        return q.delete()

    def delete(self) -> None:
        """Delete the current model record (handles soft delete if enabled)."""
        if not self.id:
            return
        self.before_delete()
        if self._soft_delete_field:
            setattr(self, self._soft_delete_field, datetime.datetime.now().isoformat())
            sql = f"UPDATE {self._table} SET {self._soft_delete_field} = ? WHERE id = ?"
            with self._get_conn() as conn:
                conn.execute(sql, (getattr(self, self._soft_delete_field), self.id))
                conn.commit()
        else:
            sql = f"DELETE FROM {self._table} WHERE id = ?"
            with self._get_conn() as conn:
                conn.execute(sql, (self.id,))
                conn.commit()
        self.after_delete()

    def force_delete(self):
        """Permanently delete, bypassing soft delete."""
        if not self.id:
            return
        self.before_delete()
        sql = f"DELETE FROM {self._table} WHERE id = ?"
        with self._get_conn() as conn:
            try:
                conn.execute(sql, (self.id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        self.after_delete()

    def restore(self):
        """Un-delete a soft-deleted row."""
        if not self.id or not self._soft_delete_field:
            return
        setattr(self, self._soft_delete_field, None)
        sql = f"UPDATE {self._table} SET {self._soft_delete_field} = NULL WHERE id = ?"
        with self._get_conn() as conn:
            try:
                conn.execute(sql, (self.id,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _pivot_info(self, rel):
        """Compute pivot table name and FK column names for BelongsToMany."""
        a = self.__class__.__name__.lower()
        b = rel.target_model_name.lower()
        pivot = rel.pivot_table or "_".join(sorted([a, b]))
        pfk = rel.pivot_fk or f"{a}_id"
        pofk = rel.pivot_other_fk or f"{b}_id"
        return pivot, pfk, pofk

    def attach(self, relation_name, ids):
        """Insert pivot rows linking self to target ids."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        if not isinstance(ids, (list, tuple, set)):
            ids = [ids]
        sql = f"INSERT OR IGNORE INTO {pivot} ({pfk}, {pofk}) VALUES (?, ?)"
        with self._get_conn() as conn:
            try:
                for tid in ids:
                    conn.execute(sql, (self.id, tid))
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def detach(self, relation_name, ids=None):
        """Remove pivot rows. If ids is None, removes all."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        with self._get_conn() as conn:
            try:
                if ids is None:
                    conn.execute(f"DELETE FROM {pivot} WHERE {pfk} = ?", (self.id,))
                else:
                    if not isinstance(ids, (list, tuple, set)):
                        ids = [ids]
                    placeholders = ", ".join(["?"] * len(ids))
                    conn.execute(
                        f"DELETE FROM {pivot} WHERE {pfk} = ? AND {pofk} IN ({placeholders})",
                        [self.id] + list(ids),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def sync(self, relation_name, ids):
        """Replace all pivot rows for this relation with the given ids."""
        rel = self._relations.get(relation_name)
        if not rel or rel.type != "BelongsToMany":
            raise ValueError(f"No BelongsToMany relation: {relation_name}")
        pivot, pfk, pofk = self._pivot_info(rel)
        with self._get_conn() as conn:
            try:
                conn.execute(f"DELETE FROM {pivot} WHERE {pfk} = ?", (self.id,))
                if ids:
                    if not isinstance(ids, (list, tuple, set)):
                        ids = [ids]
                    values = [(self.id, tid) for tid in set(ids)]
                    conn.executemany(
                        f"INSERT INTO {pivot} ({pfk}, {pofk}) VALUES (?, ?)", values
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def with_trashed(cls):
        """Include soft-deleted rows in the query."""
        return Query(cls, with_trashed=True)

    @classmethod
    def only_trashed(cls):
        """Only return soft-deleted rows."""
        if not cls._soft_delete_field:
            return Query(cls)
        q = Query(cls, with_trashed=True)
        q._wheres.append(f"{cls._soft_delete_field} IS NOT NULL")
        return q

    @classmethod
    def paginate(cls, page: int = 1, per_page: int = 10, order_by: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        """Paginate results matching the given criteria.

        Example:
            User.paginate(page=1, per_page=10, active=1)
        """
        q = cls.query()
        for k, v in kwargs.items():
            q.where(k, v)
        if order_by:
            q.order_by(order_by)
        return q.paginate(page, per_page)
