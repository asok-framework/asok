from __future__ import annotations

from .base import BaseEngine
from .mysql import MySQLEngine
from .postgres import PostgresEngine
from .sqlite import SQLiteEngine

_ENGINES_CACHE = {}

_DEFAULT_SQLITE = "db.sqlite3"


def _create_engine(db_url: str) -> BaseEngine:
    if db_url.startswith(("postgres://", "postgresql://")):
        return PostgresEngine(db_url)
    if db_url.startswith("mysql://"):
        return MySQLEngine(db_url)
    return SQLiteEngine(db_url)


def get_engine(db_url: str | None = None) -> BaseEngine:
    """Factory: instantiate the correct database engine based on DSN/URL.

    Falls back to SQLite (``db.sqlite3``) when *db_url* is ``None`` or an
    empty string — so omitting ``DATABASE_URL`` entirely always gives SQLite.

    Accepts environment variable names (e.g. ``"CONTENT_DATABASE_URL"``) and
    resolves them automatically, mirroring the runtime ``_db_path`` behaviour.
    """
    import os

    clean_url = (db_url or "").strip() or _DEFAULT_SQLITE
    # Resolve environment variable names (e.g. "_db_path = 'CONTENT_DATABASE_URL'")
    env_val = os.environ.get(clean_url, "").strip()
    if env_val:
        clean_url = env_val

    if clean_url not in _ENGINES_CACHE:
        _ENGINES_CACHE[clean_url] = _create_engine(clean_url)
    return _ENGINES_CACHE[clean_url]
