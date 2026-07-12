from __future__ import annotations

import logging
import sqlite3
import struct
import threading
from typing import Any, Dict, List, Tuple

from ..proxy import ConnectionProxy
from ..utils import (
    _asok_cosine_similarity,
    _asok_euclidean_distance,
)
from .base import BaseEngine

logger = logging.getLogger("asok.orm")


class SQLiteTransaction:
    """Transaction context manager for SQLite with nested transaction (SAVEPOINT) support."""

    def __init__(self, engine: SQLiteEngine):
        self.engine = engine
        self.conn = engine.get_connection()
        self.sp_name = None

    def __enter__(self) -> SQLiteTransaction:
        if not hasattr(self.engine._local, "txn_level"):
            self.engine._local.txn_level = 0
        self.engine._local.txn_level += 1
        level = self.engine._local.txn_level
        if level == 1:
            self.conn.execute("BEGIN;")
        else:
            self.sp_name = f"sp_{level}"
            self.conn.execute(f"SAVEPOINT {self.sp_name};")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        level = self.engine._local.txn_level
        self.engine._local.txn_level -= 1
        if exc_type is not None:
            if level == 1:
                self.conn.execute("ROLLBACK;")
            else:
                self.conn.execute(f"ROLLBACK TO {self.sp_name};")
                self.conn.execute(f"RELEASE SAVEPOINT {self.sp_name};")
        else:
            if level == 1:
                self.conn.execute("COMMIT;")
            else:
                self.conn.execute(f"RELEASE SAVEPOINT {self.sp_name};")


class SQLiteEngine(BaseEngine):
    """SQLite engine backend using the standard library sqlite3 module."""

    def __init__(self, db_path: str):
        # Normalize the database path/URL
        if db_path.startswith("sqlite://"):
            db_path = db_path.replace("sqlite://", "", 1)
        self.db_path = db_path or "db.sqlite3"
        self._local = threading.local()

    def _resolve_db_path(self) -> str:
        import os as _os

        db_path = self.db_path
        if db_path != ":memory:" and not db_path.startswith("file:"):
            if not _os.path.isabs(db_path):
                db_path = _os.path.join(_os.getcwd(), db_path)
            parent = _os.path.dirname(db_path)
            if parent:
                _os.makedirs(parent, exist_ok=True)
        return db_path

    def get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        db_path = self._resolve_db_path()
        conn = sqlite3.connect(db_path, check_same_thread=False)

        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")

        # Register Vector functions
        conn.create_function("cosine_similarity", 2, _asok_cosine_similarity)
        conn.create_function("euclidean_distance", 2, _asok_euclidean_distance)

        # Wrap with ConnectionProxy for toolbar logging
        conn = ConnectionProxy(conn)

        self._local.conn = conn
        # Track for cleanup
        if not hasattr(self._local, "_all_conns"):
            self._local._all_conns = []
        self._local._all_conns.append(conn)
        return conn

    def close_connections(self) -> None:
        for conn in getattr(self._local, "_all_conns", []):
            try:
                conn.close()
            except Exception:
                pass
        self._local._all_conns = []
        if hasattr(self._local, "conn"):
            delattr(self._local, "conn")

    def _autocommit_if_needed(self, conn: Any) -> None:
        txn_level = getattr(self._local, "txn_level", 0)
        if txn_level == 0 and conn.in_transaction:
            conn.commit()

    def execute(
        self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None
    ) -> List[Dict[str, Any]] | int:
        conn = self.get_connection()
        query_args = args if args is not None else ()
        cursor = conn.execute(sql, query_args)
        if cursor.description:
            return [dict(row) for row in cursor.fetchall()]
        # Auto-commit DML statements when not inside an explicit transaction,
        # matching the autocommit=True behavior of the MySQL and PostgreSQL engines.
        self._autocommit_if_needed(conn)
        return cursor.rowcount

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def translate_query(
        self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None
    ) -> Tuple[str, List[Any]]:
        return sql, list(args) if args else []

    def get_column_type(self, field: Any) -> str:
        return field.sql_type

    def table_exists(self, table_name: str) -> bool:
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
        res = self.execute(sql, (table_name,))
        return len(res) > 0

    def get_table_columns(self, table_name: str) -> List[str]:
        sql = f"PRAGMA table_info({self.quote_identifier(table_name)})"
        rows = self.execute(sql)
        return [row["name"] for row in rows]

    def search_sql(
        self, table: str, columns: List[str], term: str
    ) -> Tuple[str, List[Any]]:
        import re

        clean = re.sub(r"[^\w\s]", " ", term or "", flags=re.UNICODE)
        words = clean.split()
        if not words:
            return "0 = 1", []
        ft_term = " ".join(f'"{w}"*' for w in words)
        fts_table = f"{table}_fts"
        subquery = f"SELECT rowid FROM {self.quote_identifier(fts_table)} WHERE {self.quote_identifier(fts_table)} MATCH ?"
        return f"id IN ({subquery})", [ft_term]

    def vector_distance_sql(self, column: str, metric: str) -> str:
        if metric == "cosine":
            return f"cosine_similarity({self.quote_identifier(column)}, ?) DESC"
        else:
            return f"euclidean_distance({self.quote_identifier(column)}, ?) ASC"

    def prepare_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            if isinstance(value, (bytes, bytearray)):
                return value
            return struct.pack(f"{len(value)}f", *value)
        return value

    def deserialize_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and isinstance(value, (bytes, bytearray)):
            if len(value) % 4 != 0:
                logger.warning(
                    "Vector field has invalid byte length %d (not divisible by 4)",
                    len(value),
                )
                return []
            return list(struct.unpack(f"{len(value) // 4}f", value))
        return value

    def handle_exception(self, e: Exception) -> Exception:
        if isinstance(e, sqlite3.IntegrityError):
            from ..exceptions import ModelError
            from ..utils import _RE_NOT_NULL, _RE_UNIQUE

            msg = str(e)
            m = _RE_UNIQUE.search(msg)
            if m:
                field = m.group(1)
                return ModelError(f"{field} already exists", field=field, original=e)
            m = _RE_NOT_NULL.search(msg)
            if m:
                field = m.group(1)
                return ModelError(f"{field} is required", field=field, original=e)
            return ModelError(msg, original=e)
        return e

    def _validate_and_get_search_field_names(
        self, model_class: Any
    ) -> tuple[str, str, str]:
        for field_name in model_class._search_fields:
            model_class._valid_column(field_name)

        f_names_quoted = ", ".join([f'"{n}"' for n in model_class._search_fields])
        f_names_new = ", ".join([f"new.{n}" for n in model_class._search_fields])
        f_names_old = ", ".join([f"old.{n}" for n in model_class._search_fields])
        return f_names_quoted, f_names_new, f_names_old

    def _setup_fts_tables_and_triggers(
        self, model_class: Any, f_names_quoted: str, f_names_new: str, f_names_old: str
    ) -> None:
        # Create FTS5 virtual table
        fts_sql = f'CREATE VIRTUAL TABLE IF NOT EXISTS "{model_class._table}_fts" USING fts5({f_names_quoted}, content="{model_class._table}", content_rowid="id")'
        self.execute(fts_sql)

        # Triggers to keep FTS in sync
        ai = f"""CREATE TRIGGER IF NOT EXISTS "{model_class._table}_ai" AFTER INSERT ON "{model_class._table}" BEGIN
            INSERT INTO "{model_class._table}_fts"(rowid, {f_names_quoted}) VALUES (new.id, {f_names_new});
        END;"""
        ad = f"""CREATE TRIGGER IF NOT EXISTS "{model_class._table}_ad" AFTER DELETE ON "{model_class._table}" BEGIN
            INSERT INTO "{model_class._table}_fts"("{model_class._table}_fts", rowid, {f_names_quoted}) VALUES('delete', old.id, {f_names_old});
        END;"""
        au = f"""CREATE TRIGGER IF NOT EXISTS "{model_class._table}_au" AFTER UPDATE ON "{model_class._table}" BEGIN
            INSERT INTO "{model_class._table}_fts"("{model_class._table}_fts", rowid, {f_names_quoted}) VALUES('delete', old.id, {f_names_old});
            INSERT INTO "{model_class._table}_fts"(rowid, {f_names_quoted}) VALUES (new.id, {f_names_new});
        END;"""
        self.execute(ai)
        self.execute(ad)
        self.execute(au)

    def _auto_rebuild_fts_index(self, model_class: Any) -> None:
        try:
            source_count = self.execute(
                f"SELECT COUNT(*) as cnt FROM {self.quote_identifier(model_class._table)}"
            )[0]["cnt"]
            fts_count = self.execute(
                f"SELECT COUNT(*) as cnt FROM {self.quote_identifier(model_class._table + '_fts')}"
            )[0]["cnt"]
            if source_count > 0 and fts_count == 0:
                self.execute(
                    f'INSERT INTO "{model_class._table}_fts"("{model_class._table}_fts") VALUES(\'rebuild\')'
                )
        except Exception as e:
            logger.warning(
                "Failed to rebuild FTS5 index for %s: %s", model_class._table, e
            )

    def post_create_table(self, model_class: Any) -> None:
        if model_class._search_fields:
            f_quoted, f_new, f_old = self._validate_and_get_search_field_names(
                model_class
            )
            self._setup_fts_tables_and_triggers(model_class, f_quoted, f_new, f_old)
            self._auto_rebuild_fts_index(model_class)

    @property
    def primary_key_def(self) -> str:
        return "id INTEGER PRIMARY KEY AUTOINCREMENT"

    @property
    def lastrowid_query(self) -> str | None:
        return "SELECT last_insert_rowid() AS id;"

    def transaction(self) -> Any:
        return SQLiteTransaction(self)
