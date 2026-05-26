from __future__ import annotations

from .base import BaseEngine
from .mysql import MySQLEngine
from .postgres import PostgresEngine
from .sqlite import SQLiteEngine

_ENGINES_CACHE = {}

_DEFAULT_SQLITE = "db.sqlite3"


def get_engine(db_url: str | None = None) -> BaseEngine:
    """Factory: instantiate the correct database engine based on DSN/URL.

    Falls back to SQLite (``db.sqlite3``) when *db_url* is ``None`` or an
    empty string — so omitting ``DATABASE_URL`` entirely always gives SQLite.
    """
    # Normalise: treat None / whitespace-only as the default SQLite path
    db_url = (db_url or "").strip() or _DEFAULT_SQLITE

    if db_url not in _ENGINES_CACHE:
        if db_url.startswith(("postgres://", "postgresql://")):
            _ENGINES_CACHE[db_url] = PostgresEngine(db_url)
        elif db_url.startswith("mysql://"):
            _ENGINES_CACHE[db_url] = MySQLEngine(db_url)
        else:
            _ENGINES_CACHE[db_url] = SQLiteEngine(db_url)
    return _ENGINES_CACHE[db_url]
