from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Tuple

from .base import BaseEngine

logger = logging.getLogger("asok.orm")


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

    def get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is not None and not conn.closed:
            return conn

        if not hasattr(self, "_pool_initialized"):
            self._init_pool()
            self._pool_initialized = True

        if self._pool is not None:
            conn = self._pool.getconn()
            self._local.conn = conn
            return conn

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

    def close_connections(self) -> None:
        if hasattr(self._local, "conn"):
            conn = self._local.conn
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
        import time

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
        return translated_sql, list(args) if args else []

    def get_column_type(self, field: Any) -> str:
        if getattr(field, "is_boolean", False):
            return "BOOLEAN"
        elif getattr(field, "is_json", False):
            return "JSONB"
        elif getattr(field, "is_uuid", False):
            return "UUID"
        elif getattr(field, "is_datetime", False):
            return "TIMESTAMP"
        elif getattr(field, "is_date", False):
            return "DATE"
        elif getattr(field, "is_time", False):
            return "TIME"
        elif getattr(field, "is_decimal", False):
            return f"NUMERIC({getattr(field, 'precision', 10)}, 2)"
        elif getattr(field, "is_vector", False):
            return f"vector({getattr(field, 'dimensions', 1536)})"
        elif getattr(field, "is_foreign_key", False):
            return "INTEGER"

        # Mapping base SQLite types to Postgres equivalents
        sql_type = field.sql_type.upper()
        if sql_type == "TEXT":
            # For short text fields with max_length, use VARCHAR
            max_len = getattr(field, "max_length", None)
            if max_len:
                return f"VARCHAR({max_len})"
            return "TEXT"
        elif sql_type == "INTEGER":
            return "INTEGER"
        elif sql_type == "REAL":
            return "DOUBLE PRECISION"
        elif sql_type == "BLOB":
            return "BYTEA"

        return sql_type

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
        if getattr(field, "is_boolean", False) and value is not None:
            return bool(value)
        if getattr(field, "is_vector", False) and value is not None:
            if isinstance(value, str):
                return value
            # Format as pgvector string representation: '[1.0, 2.0, ...]'
            return "[" + ",".join(map(str, value)) + "]"
        return value

    def deserialize_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            if isinstance(value, str):
                val_clean = value.strip("[]")
                return (
                    [float(x) for x in val_clean.split(",") if x] if val_clean else []
                )
            elif isinstance(value, list):
                return [float(x) for x in value]
        return value

    def handle_exception(self, e: Exception) -> Exception:
        try:
            import psycopg
        except ImportError:
            return e
        if isinstance(e, psycopg.Error):
            from ..exceptions import ModelError

            # Check unique violation (sqlstate '23505')
            if getattr(e.diag, "sqlstate", None) == "23505":
                detail = getattr(e.diag, "message_detail", "") or ""
                import re

                m = re.search(r"Key \((.*?)\)=", detail)
                field = m.group(1) if m else "field"
                return ModelError(f"{field} already exists", field=field, original=e)
            # Check not null violation (sqlstate '23502')
            elif getattr(e.diag, "sqlstate", None) == "23502":
                col = getattr(e.diag, "column_name", "") or "field"
                return ModelError(f"{col} is required", field=col, original=e)
            return ModelError(str(e), original=e)
        return e

    def post_create_table(self, model_class: Any) -> None:
        # Create pgvector extension if a vector field is present
        has_vector = any(
            getattr(f, "is_vector", False) for f in model_class._fields.values()
        )
        if has_vector:
            try:
                self.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                logger.warning("Could not ensure vector extension is installed: %s", e)

        # Create indexes for full-text search
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
