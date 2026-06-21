from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Tuple

from .base import BaseEngine

logger = logging.getLogger("asok.orm")

_RE_AUTOINCREMENT_1 = re.compile(r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', re.IGNORECASE)
_RE_AUTOINCREMENT_2 = re.compile(r'id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT', re.IGNORECASE)
_RE_VIRTUAL_TABLE = re.compile(
    r'CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"?(\w+)"?)\s+USING\s+fts5\((.*?)\)',
    re.IGNORECASE | re.DOTALL
)
_RE_CONTENT_MATCH = re.compile(r"content\s*=\s*'([^']+)'")
_RE_KEY_VIOLATION = re.compile(r"Key \((.*?)\)=")


class PostgresEngine(BaseEngine):
    """PostgreSQL engine backend using the psycopg (Psycopg 3) library."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._local = threading.local()
        self._pool = None

    def _init_pool(self) -> None:
        if self._pool is not None:
            return
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            self._pool = ConnectionPool(
                self.dsn,
                min_size=1,
                max_size=10,
                open=True,
                kwargs={"row_factory": dict_row, "autocommit": True},
            )
        except Exception:
            self._pool = None

    def _ensure_pool(self) -> None:
        if not hasattr(self, "_pool_initialized"):
            self._init_pool()
            self._pool_initialized = True

    def _connect_fallback_pg(self) -> Any:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError:
            raise ImportError(
                "PostgreSQL support requires 'psycopg'.\n"
                'Please install it using: pip install "asok[postgres]" or pip install "asok[postgres-binary]"'
            )

        conn = psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)
        self._local.conn = conn

        # Track for cleanup
        if not hasattr(self._local, "_all_conns"):
            self._local._all_conns = []
        self._local._all_conns.append(conn)
        return conn

    def get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            return conn

        self._ensure_pool()

        if self._pool is not None:
            conn = self._pool.getconn()
            self._local.conn = conn
            return conn

        return self._connect_fallback_pg()

    def _release_or_close_conn(self, conn: Any) -> None:
        if getattr(self, "_pool", None) is not None:
            try:
                self._pool.putconn(conn)
            except Exception:
                pass
        else:
            try:
                conn.close()
            except Exception:
                pass

    def close_connections(self) -> None:
        if hasattr(self._local, "conn"):
            self._release_or_close_conn(self._local.conn)
            delattr(self._local, "conn")

        for conn in getattr(self._local, "_all_conns", []):
            try:
                conn.close()
            except Exception:
                pass
        self._local._all_conns = []

    def execute(
        self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None
    ) -> List[Dict[str, Any]] | int:
        from ...context import request_var

        req = request_var.get()
        start = 0.0  # initialised early so finally block can always reference it
        try:
            conn = self.get_connection()
            start = time.time()
            translated_sql, translated_args = self.translate_query(sql, args)

            with conn.cursor() as cur:
                cur.execute(translated_sql, translated_args)
                if cur.description:
                    return cur.fetchall()
                return cur.rowcount
        finally:
            if req:
                if not hasattr(req, "_asok_sql_log"):
                    req._asok_sql_log = []
                duration = (time.time() - start) * 1000
                req._asok_sql_log.append(
                    {"sql": sql, "params": args or (), "duration": duration}
                )

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def translate_query(
        self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None
    ) -> Tuple[str, List[Any]]:
        # Translate SQLite ? to psycopg %s
        translated_sql = sql.replace("?", "%s")
        translated_sql = self._translate_autoincrement(translated_sql)
        translated_sql = self._translate_sqlite_fts(translated_sql)
        return translated_sql, list(args) if args else []

    def _translate_autoincrement(self, sql: str) -> str:
        if "AUTOINCREMENT" not in sql:
            return sql
        sql = _RE_AUTOINCREMENT_1.sub(
            'SERIAL PRIMARY KEY',
            sql
        )
        return _RE_AUTOINCREMENT_2.sub(
            'id SERIAL PRIMARY KEY',
            sql
        )

    def _translate_sqlite_fts(self, sql: str) -> str:
        if "CREATE VIRTUAL TABLE" in sql and "fts5" in sql:
            return self._build_fts_index_sql_from_virtual(sql)
        if self._is_sqlite_only_fts_query(sql):
            return "SELECT 1"
        return sql

    def _is_sqlite_only_fts_query(self, sql: str) -> bool:
        if "CREATE TRIGGER" in sql:
            return True
        if "DROP TRIGGER" in sql:
            return True
        return self._is_fts_action(sql)

    def _is_fts_action(self, sql: str) -> bool:
        if "_fts" not in sql:
            return False
        return "INSERT INTO" in sql or "DROP TABLE" in sql

    def _build_fts_index_sql_from_virtual(self, sql: str) -> str:
        match = _RE_VIRTUAL_TABLE.search(sql)
        if not match:
            return "SELECT 1"
        return self._do_build_fts(match.group(1), match.group(2))

    def _do_build_fts(self, fts_table: str, params_str: str) -> str:
        target_table = fts_table[:-4] if fts_table.endswith("_fts") else fts_table
        content_match = _RE_CONTENT_MATCH.search(params_str)
        if content_match:
            target_table = content_match.group(1)
        cols = self._extract_fts_cols(params_str)
        if not cols:
            return "SELECT 1"
        col_exprs = " || ' ' || ".join(f"coalesce(\"{c}\", '')" for c in cols)
        idx_name = f"idx_{target_table}_fts"
        return (
            f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{target_table}" '
            f'USING gin(to_tsvector(\'simple\', {col_exprs}))'
        )

    def _extract_fts_cols(self, params_str: str) -> List[str]:
        cols: List[str] = []
        for param in params_str.split(','):
            param = param.strip()
            if not param:
                continue
            if any(kw in param.lower() for kw in ('content=', 'content_rowid=')):
                continue
            cols.append(param.strip('"`[]'))
        return cols


    def _detect_pg_type_attrs(self, field: Any) -> str | None:
        """Check field type attributes and return PostgreSQL type, or None for standard mapping."""
        mappings = (
            ("is_boolean", "BOOLEAN"),
            ("is_json", "JSONB"),
            ("is_uuid", "UUID"),
            ("is_datetime", "TIMESTAMP"),
            ("is_date", "DATE"),
            ("is_time", "TIME"),
            ("is_foreign_key", "INTEGER"),
        )
        for attr, pg_type in mappings:
            if getattr(field, attr, False):
                return pg_type

        if getattr(field, "is_decimal", False):
            return f"NUMERIC({getattr(field, 'precision', 10)}, 2)"
        if getattr(field, "is_vector", False):
            return f"vector({getattr(field, 'dimensions', 1536)})"
        return None

    def _map_pg_sql_type(self, field: Any) -> str:
        """Map SQLite sql_type to its PostgreSQL equivalent."""
        sql_type = field.sql_type.upper()
        if sql_type == "TEXT":
            max_len = getattr(field, "max_length", None)
            if max_len:
                return f"VARCHAR({max_len})"
            return "TEXT"

        mappings = {
            "INTEGER": "INTEGER",
            "REAL": "DOUBLE PRECISION",
            "BLOB": "BYTEA",
        }
        return mappings.get(sql_type, sql_type)

    def get_column_type(self, field: Any) -> str:
        result = self._detect_pg_type_attrs(field)
        return result if result is not None else self._map_pg_sql_type(field)

    def table_exists(self, table_name: str) -> bool:
        sql = "SELECT EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = ?)"
        res = self.execute(sql, (table_name,))
        return res[0]["exists"] if res else False

    def get_table_columns(self, table_name: str) -> List[str]:
        sql = "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = ?"
        rows = self.execute(sql, (table_name,))
        return [row["column_name"] for row in rows]

    def search_sql(
        self, table: str, columns: List[str], term: str
    ) -> Tuple[str, List[Any]]:
        col_expr = " || ' ' || ".join(
            [f"coalesce({self.quote_identifier(c)}, '')" for c in columns]
        )
        # Using simple language search configuration
        where_clause = (
            f"to_tsvector('simple', {col_expr}) @@ plainto_tsquery('simple', ?)"
        )
        return where_clause, [term]

    def vector_distance_sql(self, column: str, metric: str) -> str:
        # pgvector uses <=> for cosine distance and <-> for Euclidean distance
        if metric == "cosine":
            return f"{self.quote_identifier(column)} <=> ?"
        else:
            return f"{self.quote_identifier(column)} <-> ?"

    def prepare_value(self, field: Any, value: Any) -> Any:
        if value is None:
            return None
        if getattr(field, "is_boolean", False):
            return bool(value)
        if getattr(field, "is_vector", False):
            if isinstance(value, str):
                return value
            return "[" + ",".join(map(str, value)) + "]"
        return value

    def _deserialize_vector_string(self, val_str: str) -> list[float]:
        val_clean = val_str.strip("[]")
        if not val_clean:
            return []
        return [float(x) for x in val_clean.split(",") if x]

    def _deserialize_vector(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._deserialize_vector_string(value)
        if isinstance(value, list):
            return [float(x) for x in value]
        return value

    def deserialize_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            return self._deserialize_vector(value)
        return value

    def _get_psycopg(self) -> Any:
        try:
            import psycopg
            return psycopg
        except ImportError:
            return None

    def _handle_unique_violation_pg(self, e: Any) -> Exception:
        from ..exceptions import ModelError
        detail = getattr(e.diag, "message_detail", "") or ""
        m = _RE_KEY_VIOLATION.search(detail)
        field = m.group(1) if m else "field"
        return ModelError(f"{field} already exists", field=field, original=e)

    def _handle_not_null_violation_pg(self, e: Any) -> Exception:
        from ..exceptions import ModelError
        col = getattr(e.diag, "column_name", "") or "field"
        return ModelError(f"{col} is required", field=col, original=e)

    def handle_exception(self, e: Exception) -> Exception:
        psycopg = self._get_psycopg()
        if psycopg is None:
            return e
        if isinstance(e, psycopg.Error):
            sqlstate = getattr(e.diag, "sqlstate", None)
            if sqlstate == "23505":
                return self._handle_unique_violation_pg(e)
            if sqlstate == "23502":
                return self._handle_not_null_violation_pg(e)
            from ..exceptions import ModelError
            return ModelError(str(e), original=e)
        return e

    def _setup_vector_extension_pg(self, model_class: Any) -> None:
        has_vector = any(
            getattr(f, "is_vector", False) for f in model_class._fields.values()
        )
        if has_vector:
            try:
                self.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                logger.warning("Could not ensure vector extension is installed: %s", e)

    def _setup_fts_index_pg(self, model_class: Any) -> None:
        if model_class._search_fields:
            col_expr = " || ' ' || ".join(
                [
                    f"coalesce({self.quote_identifier(c)}, '')"
                    for c in model_class._search_fields
                ]
            )
            index_name = f"idx_{model_class._table}_fts"
            try:
                self.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.quote_identifier(index_name)} ON {self.quote_identifier(model_class._table)} USING gin(to_tsvector('simple', {col_expr}));"
                )
            except Exception as e:
                logger.warning(
                    "Failed to create GIN search index for %s: %s",
                    model_class._table,
                    e,
                )

    def post_create_table(self, model_class: Any) -> None:
        self._setup_vector_extension_pg(model_class)
        self._setup_fts_index_pg(model_class)

    def transaction(self) -> Any:
        if not hasattr(self._local, "txn_level"):
            self._local.txn_level = 0
        conn_txn = self.get_connection().transaction()

        class PostgresTransactionWrapper:
            def __init__(self, txn: Any, engine: PostgresEngine):
                self.txn = txn
                self.engine = engine

            def __enter__(self) -> Any:
                self.engine._local.txn_level += 1
                return self.txn.__enter__()

            def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
                self.engine._local.txn_level -= 1
                return self.txn.__exit__(exc_type, exc_val, exc_tb)

        return PostgresTransactionWrapper(conn_txn, self)

    @property
    def primary_key_def(self) -> str:
        return "id SERIAL PRIMARY KEY"

    @property
    def lastrowid_query(self) -> str | None:
        return "SELECT lastval() AS id;"
