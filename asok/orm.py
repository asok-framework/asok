from __future__ import annotations

import binascii
import datetime
import decimal
import enum
import hashlib
import hmac
import json
import logging
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

from .events import events
from .exceptions import AsokException

T = TypeVar("T", bound="Model")
logger = logging.getLogger(__name__)

# SECURITY: SQL identifier validation regex (alphanumeric + underscore, 1-64 chars)
_SQL_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")

# SQLite reserved keywords that should not be used as identifiers
_SQL_RESERVED_WORDS = {
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "CREATE",
    "ALTER",
    "TABLE",
    "INDEX",
    "VIEW",
    "TRIGGER",
    "FROM",
    "WHERE",
    "JOIN",
    "ON",
    "AS",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "TRUE",
    "FALSE",
    "UNION",
    "INTERSECT",
    "EXCEPT",
    "GROUP",
    "BY",
    "ORDER",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "DISTINCT",
    "ALL",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "IN",
    "BETWEEN",
    "LIKE",
    "GLOB",
    "IS",
    "ISNULL",
    "NOTNULL",
    "EXISTS",
    "CAST",
    "COLLATE",
    "ASC",
    "DESC",
    "DEFAULT",
    "CONSTRAINT",
    "PRIMARY",
    "KEY",
    "FOREIGN",
    "REFERENCES",
    "UNIQUE",
    "CHECK",
    "AUTOINCREMENT",
    "CASCADE",
    "RESTRICT",
    "SET",
    "NO",
    "ACTION",
    "PRAGMA",
    "TRANSACTION",
    "BEGIN",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "RELEASE",
    "VACUUM",
    "ANALYZE",
    "EXPLAIN",
    "ATTACH",
    "DETACH",
}


def validate_sql_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL identifier (table/column/index name) to prevent SQL injection.

    SECURITY: This function prevents SQL injection in dynamic schema operations
    by ensuring identifiers only contain safe characters and aren't SQL keywords.

    Args:
        name: The identifier to validate
        context: Description of the identifier for error messages

    Returns:
        The validated identifier (unchanged if valid)

    Raises:
        ValueError: If the identifier is invalid or a SQL keyword
    """
    if not name or not isinstance(name, str):
        raise ValueError(f"Invalid SQL {context}: identifier cannot be empty")

    # Check format: alphanumeric + underscore, max 64 chars
    if not _SQL_IDENTIFIER_PATTERN.match(name):
        raise ValueError(
            f"Invalid SQL {context}: '{name}'. "
            f"Must start with letter/underscore, contain only alphanumeric "
            f"characters and underscores, and be max 64 characters long."
        )

    # Check against reserved words (case-insensitive)
    if name.upper() in _SQL_RESERVED_WORDS:
        raise ValueError(
            f"Invalid SQL {context}: '{name}' is a SQL reserved word. "
            f"Please use a different name or add a prefix/suffix."
        )

    return name


def convert_sql_to_text(obj: Any) -> str:
    """Helper to extract the raw SQL from a Query object or a Model.
    Example: convert_sql_to_text(Product.query().where('id', 1))

    WARNING: For debugging only. Naive interpolation, not secure for execution.
    """
    if hasattr(obj, "raw_sql"):
        return obj.raw_sql()
    if hasattr(obj, "query") and callable(obj.query):
        return obj.query().raw_sql()
    return str(obj)


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
    """English pluralization with snake_case conversion for table names."""
    if not word:
        return word
    # CamelCase to snake_case
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", word)
    word = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if word.endswith("y") and len(word) > 1:
        return word[:-1] + "ies"
    return word + "s"


class ModelError(AsokException):
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


def interpolate_sql(sql: str, args: list) -> str:
    """Return the SQL query with parameters interpolated (for debugging only).
    WARNING: This is naive and NOT SECURE against SQL injection.
    Use only for inspection in logs/console; never execute this string.
    """
    if not sql:
        return ""
    if not args:
        return sql

    # Naive interpolation for inspection
    res = sql
    for arg in args:
        if isinstance(arg, str):
            escaped = arg.replace("'", "''")
            val = f"'{escaped}'"
        elif arg is None:
            val = "NULL"
        elif isinstance(arg, (int, float)):
            val = str(arg)
        elif isinstance(arg, bool):
            val = "1" if arg else "0"
        else:
            val = f"'{str(arg)}'"
        res = res.replace("?", val, 1)
    return res


class ModelList(list):
    """List subclass that supports .count() without arguments and SQL inspection."""

    def __init__(self, iterable=None, sql: str = None, args: list = None):
        super().__init__(iterable or [])
        self._sql = sql
        self._args = args or []

    def count(self, value=None):
        """Return the number of items in the list.
        If 'value' is provided, returns the number of occurrences of 'value'.
        """
        if value is not None:
            return super().count(value)
        return len(self)

    def to_sql(self) -> str:
        """Return the SQL query string with placeholders."""
        return self._sql or ""

    def raw_sql(self) -> str:
        """Return the SQL query with parameters interpolated (for debugging only)."""
        return interpolate_sql(self._sql, self._args)


class ConnectionProxy:
    """A wrapper for SQLite connections that logs execution time and queries for the Developer Toolbar."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, parameters=()):
        """Execute a SQL statement and log its performance to the current request context."""
        from .context import request_var

        req = request_var.get()
        if not req:
            return self._conn.execute(sql, parameters)

        if not hasattr(req, "_asok_sql_log"):
            req._asok_sql_log = []
        import time

        start = time.time()
        try:
            return self._conn.execute(sql, parameters)
        finally:
            duration = (time.time() - start) * 1000
            req._asok_sql_log.append(
                {"sql": sql, "params": parameters, "duration": duration}
            )

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)


# Registry for all models to enable cross-model relationships
MODELS_REGISTRY = {}


class Migrations:
    """Utility to track and manage applied database migrations."""

    @staticmethod
    def ensure_table():
        """Ensures the tracking table exists in the database."""
        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _asok_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    batch INTEGER NOT NULL,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_applied() -> list[str]:
        """Return a list of all applied migration names in chronological order."""
        Migrations.ensure_table()
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT name FROM _asok_migrations ORDER BY id ASC"
            ).fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def log(name: str, batch: int):
        """Record a new migration as applied."""
        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute(
                "INSERT INTO _asok_migrations (name, batch) VALUES (?, ?)",
                (name, batch),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_last_batch_number() -> int:
        """Return the current maximum batch number."""
        Migrations.ensure_table()
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT MAX(batch) as max_batch FROM _asok_migrations"
            ).fetchone()
            return row["max_batch"] or 0
        finally:
            conn.close()

    @staticmethod
    def get_last_batch() -> list[str]:
        """Return names of migrations belonging to the last executed batch."""
        last_batch = Migrations.get_last_batch_number()
        if last_batch == 0:
            return []
        conn = sqlite3.connect(Model._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT name FROM _asok_migrations WHERE batch = ? ORDER BY id DESC",
                (last_batch,),
            ).fetchall()
            return [row["name"] for row in rows]
        finally:
            conn.close()

    @staticmethod
    def remove(name: str):
        """Remove a migration record from the tracking table."""
        conn = sqlite3.connect(Model._db_path)
        try:
            conn.execute("DELETE FROM _asok_migrations WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()


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
        index: bool = False,
        form_type: Optional[str] = None,
        **kwargs,
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
        self._select: str = "*"
        self._wheres: list[str] = []
        self._args: list[Any] = []
        self._order: Optional[str] = None
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._groups: list[str] = []
        self._eager: list[str] = []
        self._union_queries: list[Query[T]] = []
        self._intersect_queries: list[Query[T]] = []
        # Auto-filter soft-deleted rows unless explicitly included
        if model._soft_delete_field and not with_trashed:
            self._wheres.append(f"{model._soft_delete_field} IS NULL")

    def with_(self, *relation_names: str) -> Query[T]:
        """Eager load relationships to avoid N+1 query problems."""
        self._eager.extend(relation_names)
        return self

    def cache(self, ttl: int = 60, key: Optional[str] = None) -> Query[T]:
        """Enable caching for this query."""
        self._cache_ttl = ttl
        self._cache_key = key
        return self

    def select(self, *columns: str) -> Query[T]:
        """Set specific columns to select (useful for aggregates or partial loads)."""
        valid_cols = []
        for col in columns:
            col_strip = col.strip()
            # Allow *
            if col_strip == "*":
                valid_cols.append(col_strip)
                continue

            # Allow simple column names
            if self.model._valid_column(col_strip):
                valid_cols.append(col_strip)
                continue

            # Allow common aggregates e.g. COUNT(*) or SUM(price) as total
            # Regex to match FUNC(col) [AS alias]
            match = re.match(
                r"^(COUNT|SUM|AVG|MIN|MAX)\((.*?)\)(?:\s+AS\s+(\w+))?$", col_strip, re.I
            )
            if match:
                func, inner, alias = match.groups()
                inner_strip = inner.strip()
                if inner_strip == "*" or self.model._valid_column(inner_strip):
                    valid_cols.append(col_strip)
                    continue

            raise ValueError(f"Invalid column or expression for selection: {col}")

        self._select = ", ".join(valid_cols)
        return self

    def group_by(self, *columns: str) -> Query[T]:
        """Add a GROUP BY clause to the query."""
        for col in columns:
            if not self.model._valid_column(col):
                raise ValueError(f"Invalid column for grouping: {col}")
        self._groups.extend(columns)
        return self

    def union(self, other: Query[T]) -> Query[T]:
        """Combine results with another query using UNION (removes duplicates).

        Example:
            admins = User.where('role', 'admin')
            mods = User.where('role', 'moderator')
            staff = admins.union(mods)
        """
        if not isinstance(other, Query):
            raise ValueError("union() requires another Query object")
        if other.model != self.model:
            raise ValueError("Cannot union queries from different models")
        self._union_queries.append(other)
        return self

    def intersect(self, other: Query[T]) -> Query[T]:
        """Get only results that appear in both queries using INTERSECT.

        Example:
            active = User.where('active', 1)
            premium = User.where('premium', 1)
            active_premium = active.intersect(premium)
        """
        if not isinstance(other, Query):
            raise ValueError("intersect() requires another Query object")
        if other.model != self.model:
            raise ValueError("Cannot intersect queries from different models")
        self._intersect_queries.append(other)
        return self

    def __getattr__(self, name: str):
        """Allow calling scope methods defined on the model (e.g. scope_active)."""
        scope_method = f"scope_{name}"
        if hasattr(self.model, scope_method):
            method = getattr(self.model, scope_method)
            # Return a wrapper that passes 'self' (the query) as first argument
            return lambda *args, **kwargs: method(self, *args, **kwargs)
        raise AttributeError(
            f"'{self.__class__.__name__}' object or model '{self.model.__name__}' has no attribute '{name}'"
        )

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

    def where_in(self, column: str, values) -> Query[T]:
        """Filter by a list of values or a subquery.

        Args:
            column: The column name to filter
            values: Either a list of values OR a Query object (subquery)

        Example with list:
            User.where_in('id', [1, 2, 3])

        Example with subquery:
            active_user_ids = User.query().where('active', 1).select('id')
            Post.where_in('user_id', active_user_ids)
        """
        if not self.model._valid_column(column):
            raise ValueError(f"Invalid column: {column}")

        # Check if values is a Query (subquery)
        if isinstance(values, Query):
            subquery_sql = values._build()
            self._wheres.append(f"{column} IN ({subquery_sql})")
            self._args.extend(values._args)
            return self

        # Regular list of values
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

    def search(self, term: str) -> Query[T]:
        """Perform a full-text search against indexed fields."""
        if not self.model._search_fields:
            return self

        if term and "*" not in term:
            term = " ".join([f"{t}*" for t in term.split() if t])

        subquery = f"SELECT rowid FROM {self.model._table}_fts WHERE {self.model._table}_fts MATCH ?"
        self._wheres.append(f"id IN ({subquery})")
        self._args.append(term)
        return self

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

    def to_sql(self) -> str:
        """Return the SQL query string with placeholders."""
        return self._build()

    def raw_sql(self) -> str:
        """Return the SQL query with parameters interpolated (for debugging only).
        WARNING: This is naive and NOT SECURE against SQL injection.
        Use only for inspection in logs/console; never execute this string.
        """
        all_args = list(self._args)
        for u in self._union_queries:
            all_args.extend(u._args)
        for i in self._intersect_queries:
            all_args.extend(i._args)
        return interpolate_sql(self.to_sql(), all_args)

    def __repr__(self) -> str:
        return f"<Query: {self.to_sql()}>"

    def _build(self, select: Optional[str] = None) -> str:
        """Internal helper to construct the SQL query string."""
        sel = select or self._select
        sql = f"SELECT {sel} FROM {self.model._table}"
        sql += self._build_where()
        if self._groups:
            sql += f" GROUP BY {', '.join(self._groups)}"

        # Add UNION queries
        for union_query in self._union_queries:
            union_sql = f"SELECT {union_query._select} FROM {union_query.model._table}"
            union_sql += union_query._build_where()
            if union_query._groups:
                union_sql += f" GROUP BY {', '.join(union_query._groups)}"
            sql = f"({sql}) UNION ({union_sql})"

        # Add INTERSECT queries
        for intersect_query in self._intersect_queries:
            intersect_sql = (
                f"SELECT {intersect_query._select} FROM {intersect_query.model._table}"
            )
            intersect_sql += intersect_query._build_where()
            if intersect_query._groups:
                intersect_sql += f" GROUP BY {', '.join(intersect_query._groups)}"
            sql = f"({sql}) INTERSECT ({intersect_sql})"

        # ORDER/LIMIT/OFFSET apply to the final result
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

        # Collect all args from this query and any union/intersect queries
        all_args = list(self._args)
        for union_query in self._union_queries:
            all_args.extend(union_query._args)
        for intersect_query in self._intersect_queries:
            all_args.extend(intersect_query._args)

        cache_ttl = getattr(self, "_cache_ttl", None)
        if cache_ttl is not None:
            import hashlib

            from .cache import default_cache

            if hasattr(self, "_cache_key") and self._cache_key:
                cache_key = self._cache_key
            else:
                raw_key = f"{sql}_{all_args}_{self._eager}"
                cache_key = "orm_" + hashlib.md5(raw_key.encode()).hexdigest()

            cached = default_cache.get(cache_key)
            if cached is not None:
                return cached

        with self.model._get_conn() as conn:
            rows = conn.execute(sql, all_args).fetchall()
        results = ModelList(
            (self.model(_trust=True, **dict(row)) for row in rows),
            sql=sql,
            args=all_args,
        )
        if self._eager and results:
            self._load_eager(results)

        if getattr(self, "_cache_ttl", None) is not None:
            from .cache import default_cache

            # Ensure cache_key is available in this scope
            cache_key = (
                getattr(self, "_cache_key", None)
                or "orm_"
                + __import__("hashlib")
                .md5(f"{sql}_{all_args}_{self._eager}".encode())
                .hexdigest()
            )
            default_cache.set(cache_key, results, ttl=self._cache_ttl)

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
    """Metaclass for all Asok Models.
    Handles field discovery, relationship mapping, and automatic table name generation.
    """

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
                    return ModelList(
                        (target_model(**dict(row)) for row in rows),
                        sql=sql,
                        args=[self.id],
                    )

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

    _db_path: str = os.getenv("DATABASE_URL", "db.sqlite3")

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

        # Handle extra fields (e.g. aggregates from GROUP BY)
        if _trust:
            for k, v in kwargs.items():
                if k not in self._fields and k != "id":
                    setattr(self, k, v)

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
        iterations = 600000  # OWASP 2023 recommendation
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
        try:
            # Use mode=rw to prevent automatic file creation during runtime.
            # The file should only be created by 'asok migrate' or 'asok make migration'.
            conn = sqlite3.connect(f"file:{cls._db_path}?mode=rw", uri=True)
        except sqlite3.OperationalError:
            # Database file does not exist yet. Return an in-memory connection
            # to prevent file creation and allow read operations to fail gracefully (no such table).
            conn = sqlite3.connect(":memory:")

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        # ASOK VECTOR EXTENSION
        conn.create_function("cosine_similarity", 2, _asok_cosine_similarity)
        conn.create_function("euclidean_distance", 2, _asok_euclidean_distance)

        # SQL LOGGING FOR DEVELOPER TOOLBAR
        conn = ConnectionProxy(conn)

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
        """Create the table if it doesn't exist, or migrate it by adding missing columns."""
        # SECURITY: Validate table name to prevent SQL injection
        validate_sql_identifier(cls._table, "table name")

        f_defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for name, f in cls._fields.items():
            # SECURITY: Validate column name to prevent SQL injection
            validate_sql_identifier(name, "column name")
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

            # ── AUTO-MIGRATION: Add missing columns ──
            existing_cols = [
                row[1]
                for row in conn.execute(f"PRAGMA table_info({cls._table})").fetchall()
            ]
            for name, f in cls._fields.items():
                if name not in existing_cols:
                    # SECURITY: Validate column name (already validated above, but double-check for migrations)
                    validate_sql_identifier(name, "column name")
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

                    logger.info("Migrating %s: Adding column %s", cls._table, name)
                    try:
                        conn.execute(f"ALTER TABLE {cls._table} ADD COLUMN {def_str}")
                    except Exception as e:
                        logger.error(
                            "Failed to migrate %s (adding %s): %s", cls._table, name, e
                        )

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

                # Auto-rebuild if FTS is empty but source has data
                try:
                    source_count = conn.execute(
                        f"SELECT COUNT(*) FROM {cls._table}"
                    ).fetchone()[0]
                    fts_count = conn.execute(
                        f"SELECT COUNT(*) FROM {cls._table}_fts"
                    ).fetchone()[0]
                    if source_count > 0 and fts_count == 0:
                        conn.execute(
                            f"INSERT INTO {cls._table}_fts({cls._table}_fts) VALUES('rebuild')"
                        )
                except Exception:
                    pass

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

                        # SECURITY: Validate all identifiers to prevent SQL injection
                        validate_sql_identifier(pivot_table, "pivot table name")
                        validate_sql_identifier(pivot_fk, "pivot foreign key")
                        validate_sql_identifier(pivot_other_fk, "pivot foreign key")

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

            # Create indexes for fields marked with index=True
            for field_name, field in cls._fields.items():
                if getattr(field, "index", False) and not field.unique:
                    # SECURITY: Validate identifiers (field_name already validated above)
                    index_name = f"idx_{cls._table}_{field_name}"
                    validate_sql_identifier(index_name, "index name")
                    index_sql = f"CREATE INDEX IF NOT EXISTS {index_name} ON {cls._table}({field_name})"
                    try:
                        conn.execute(index_sql)
                        logger.info(
                            "Created index %s on %s.%s",
                            index_name,
                            cls._table,
                            field_name,
                        )
                    except Exception as e:
                        logger.error(
                            "Failed to create index %s on %s.%s: %s",
                            index_name,
                            cls._table,
                            field_name,
                            e,
                        )

            # Commit all schema changes (explicit commit like in save())
            conn.commit()

    @classmethod
    def create(cls: type[T], _trust: bool = False, **kwargs: Any) -> T:
        """Create a new model instance, save it to the database, and return it."""
        obj = cls(_trust=_trust, **kwargs)
        obj.save()
        return obj

    # ── Model event hooks (override in subclasses) ───────────
    def before_save(self):
        """Called before a model is persisted to the database (both on create and update)."""
        pass

    def after_save(self):
        """Called after a model is successfully saved to the database."""
        pass

    def before_create(self):
        """Called only before a new model record is created."""
        pass

    def after_create(self):
        """Called only after a new model record is successfully created."""
        pass

    def before_update(self):
        """Called only before an existing model record is updated."""
        pass

    def after_update(self):
        """Called only after an existing model record is successfully updated."""
        pass

    def before_delete(self):
        """Called before a model record is deleted from the database."""
        pass

    def after_delete(self):
        """Called after a model record is successfully deleted."""
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
            events.emit(f"model:{self.__class__.__name__}:created", self)
            events.emit("model:created", self)
        else:
            self.after_update()
            events.emit(f"model:{self.__class__.__name__}:{self.id}:updated", self)
            events.emit(f"model:{self.__class__.__name__}:updated", self)
            events.emit("model:updated", self)
        self.after_save()
        events.emit("model:saved", self)

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
            return ModelList(
                (cls(_trust=True, **dict(row)) for row in rows), sql=sql, args=args
            )

    @classmethod
    def count(cls, **kwargs):
        """Return the total number of records matching the given criteria."""
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
        """Return True if at least one record exists matching the given criteria."""
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

        # Preparation du terme pour FTS5 (prefix search par defaut sur chaque mot)
        if term and "*" not in term:
            term = " ".join([f"{t}*" for t in term.split() if t])

        # Try FTS5 specific query first
        sql_fts5 = f"""
            SELECT t.* FROM "{cls._table}" t
            JOIN "{cls._table}_fts" f ON t.id = f.rowid
            WHERE f.{cls._table}_fts MATCH ? {sd_where}
            ORDER BY rank
            LIMIT ? OFFSET ?
        """

        # Fallback for FTS4 or other issues
        sql_fallback = f"""
            SELECT t.* FROM "{cls._table}" t
            JOIN "{cls._table}_fts" f ON t.id = f.rowid
            WHERE f.{cls._table}_fts MATCH ? {sd_where}
            LIMIT ? OFFSET ?
        """

        sql_used = sql_fts5
        with cls._get_conn() as conn:
            try:
                # Try FTS5
                rows = conn.execute(sql_fts5, (term, limit, offset)).fetchall()
            except sqlite3.OperationalError:
                # If it's just a syntax error in FTS5 or missing rank, try fallback
                sql_used = sql_fallback
                try:
                    rows = conn.execute(sql_fallback, (term, limit, offset)).fetchall()
                except sqlite3.OperationalError as e2:
                    logger.error(
                        "FTS search failed for %s: fallback also failed: %s",
                        cls._table,
                        e2,
                    )
                    return ModelList()

        return ModelList(
            (cls(_trust=True, **dict(row)) for row in rows),
            sql=sql_used,
            args=[term, limit, offset],
        )

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
                    stacklevel=2,
                )
                break

        with cls._get_conn() as conn:
            rows = conn.execute(sql, args or []).fetchall()
        return ModelList(
            (cls(_trust=True, **dict(row)) for row in rows), sql=sql, args=args or []
        )

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
    def paginate(
        cls,
        page: int = 1,
        per_page: int = 10,
        order_by: Optional[str] = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
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
