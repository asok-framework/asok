from __future__ import annotations

import math
import re
import struct
import threading
import unicodedata
from typing import Any

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

_RE_UNIQUE = re.compile(r"UNIQUE constraint failed: \w+\.(\w+)")
_RE_NOT_NULL = re.compile(r"NOT NULL constraint failed: \w+\.(\w+)")
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_RE_TEL = re.compile(r"^\+?[\d\s\-\.]{7,20}$")

_RE_SLUG_STRIP = re.compile(r"[^\w\s-]")
_RE_SLUG_SEP = re.compile(r"[-\s]+")

# Thread-local storage for reusing SQLite connections
_local = threading.local()

# Registry for all models to enable cross-model relationships
MODELS_REGISTRY = {}


def validate_sql_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL identifier (table/column/index name) to prevent SQL injection.

    SECURITY: This function prevents SQL injection in dynamic schema operations
    by ensuring identifiers only contain safe characters and aren't SQL keywords.
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


def _numpy_cosine_similarity(v1: bytes, v2: bytes) -> float:
    import numpy as np
    a = np.frombuffer(v1, dtype=np.float32)
    b = np.frombuffer(v2, dtype=np.float32)
    if a.shape != b.shape:
        return 0.0
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def _unpack_float_vectors(v1: bytes, v2: bytes) -> tuple[tuple[float, ...], tuple[float, ...]] | None:
    import logging
    if len(v1) % 4 != 0 or len(v2) % 4 != 0:
        logging.getLogger("asok.orm").debug(
            "Vector byte length not divisible by 4: %d, %d", len(v1), len(v2)
        )
        return None
    a = struct.unpack(f"{len(v1) // 4}f", v1)
    b = struct.unpack(f"{len(v2) // 4}f", v2)
    if len(a) != len(b):
        return None
    return a, b


def _dot_product(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _l2_norm(v: tuple[float, ...]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _python_cosine_similarity(v1: bytes, v2: bytes) -> float:
    vectors = _unpack_float_vectors(v1, v2)
    if not vectors:
        return 0.0
    a, b = vectors
    dot = _dot_product(a, b)
    n1 = _l2_norm(a)
    n2 = _l2_norm(b)
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def _asok_cosine_similarity(v1, v2):
    """SQLite extension for cosine similarity: 1 - cosine_distance."""
    import logging
    if not v1 or not v2:
        return 0.0
    try:
        try:
            return _numpy_cosine_similarity(v1, v2)
        except ImportError:
            return _python_cosine_similarity(v1, v2)
    except Exception as e:
        # Log vector operation errors for debugging
        logging.getLogger("asok.orm").debug("Error in cosine_similarity: %s", e)
        return 0.0


def _numpy_euclidean_distance(v1: bytes, v2: bytes) -> float:
    import numpy as np
    a = np.frombuffer(v1, dtype=np.float32)
    b = np.frombuffer(v2, dtype=np.float32)
    if a.shape != b.shape:
        return 99999.0
    return float(np.linalg.norm(a - b))


def _python_euclidean_distance(v1: bytes, v2: bytes) -> float:
    vectors = _unpack_float_vectors(v1, v2)
    if not vectors:
        return 99999.0
    a, b = vectors
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _asok_euclidean_distance(v1, v2):
    """SQLite extension for euclidean distance."""
    import logging
    if not v1 or not v2:
        return 99999.0
    try:
        try:
            return _numpy_euclidean_distance(v1, v2)
        except ImportError:
            return _python_euclidean_distance(v1, v2)
    except Exception as e:
        # Log vector operation errors for debugging
        logging.getLogger("asok.orm").debug("Error in euclidean_distance: %s", e)
        return 99999.0


def _format_sql_arg(arg: Any) -> str:
    if isinstance(arg, str):
        escaped = arg.replace("'", "''")
        return f"'{escaped}'"
    if arg is None:
        return "NULL"
    if isinstance(arg, bool):
        return "1" if arg else "0"
    return _format_sql_numeric_or_fallback(arg)


def _format_sql_numeric_or_fallback(arg: Any) -> str:
    if isinstance(arg, (int, float)):
        return str(arg)
    return f"'{str(arg)}'"


def interpolate_sql(sql: str, args: list) -> str:
    """Return the SQL query with parameters interpolated (for debugging only).

    WARNING: This is naive and NOT SECURE against SQL injection.
    Use only for inspection in logs/console; never execute this string.
    """
    if not sql:
        return ""
    if not args:
        return sql

    res = sql
    for arg in args:
        val = _format_sql_arg(arg)
        res = res.replace("?", val, 1)
    return res


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
