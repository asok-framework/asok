from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from .base import BaseEngine

logger = logging.getLogger("asok.orm")

class MySQLTransaction:
    """Transaction context manager for MySQL."""

    def __init__(self, conn: Any):
        self.conn = conn

    def __enter__(self) -> MySQLTransaction:
        self.conn.begin()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            self.conn.rollback()
        else:
            self.conn.commit()

class MySQLEngine(BaseEngine):
    """MySQL engine backend using the pymysql library."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._local = threading.local()

    def get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is not None and conn.open:
            return conn

        try:
            import pymysql
            import pymysql.cursors
        except ImportError:
            raise ImportError(
                "MySQL support requires 'pymysql'.\n"
                "Please install it using: pip install \"asok[mysql]\""
            )

        # Parse DSN (mysql://user:password@host:port/database)
        parsed = urlparse(self.dsn)
        host = parsed.hostname or "localhost"
        port = parsed.port or 3306
        user = parsed.username or "root"
        password = parsed.password or ""
        db = parsed.path.lstrip("/")

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )
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

    def execute(self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None) -> List[Dict[str, Any]] | int:
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
                    return list(cur.fetchall())
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
        # MySQL uses backticks
        return f"`{name}`"

    def translate_query(self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None) -> Tuple[str, List[Any]]:
        # Translate ? to %s
        translated_sql = sql.replace("?", "%s")
        return translated_sql, list(args) if args else []

    def get_column_type(self, field: Any) -> str:
        if getattr(field, "is_boolean", False):
            return "TINYINT(1)"
        elif getattr(field, "is_json", False):
            return "JSON"
        elif getattr(field, "is_uuid", False):
            return "VARCHAR(36)"
        elif getattr(field, "is_datetime", False):
            return "DATETIME"
        elif getattr(field, "is_date", False):
            return "DATE"
        elif getattr(field, "is_time", False):
            return "TIME"
        elif getattr(field, "is_decimal", False):
            return f"DECIMAL({getattr(field, 'precision', 10)}, 2)"
        elif getattr(field, "is_vector", False):
            # MySQL has no native vector support, store as JSON array
            return "JSON"
        elif getattr(field, "is_foreign_key", False):
            return "INTEGER"

        sql_type = field.sql_type.upper()
        if sql_type == "TEXT":
            max_len = getattr(field, "max_length", None)
            if max_len:
                return f"VARCHAR({max_len})"
            return "TEXT"
        elif sql_type == "INTEGER":
            return "INT"
        elif sql_type == "REAL":
            return "DOUBLE"
        elif sql_type == "BLOB":
            return "LONGBLOB"

        return sql_type

    def table_exists(self, table_name: str) -> bool:
        sql = "SELECT COUNT(*) as cnt FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = ?"
        res = self.execute(sql, (table_name,))
        return res[0]["cnt"] > 0 if res else False

    def get_table_columns(self, table_name: str) -> List[str]:
        sql = "SELECT column_name FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = ?"
        rows = self.execute(sql, (table_name,))
        return [row["column_name"] for row in rows]

    # MySQL/MariaDB default minimum full-text word length (innodb_ft_min_token_size)
    _FT_MIN_WORD_LEN: int = 3

    def search_sql(self, table: str, columns: List[str], term: str) -> Tuple[str, List[Any]]:
        import re
        cols = ", ".join([self.quote_identifier(c) for c in columns])

        # Sanitize: keep only alphanumeric chars and spaces; strip FTS operators
        # that the user may have accidentally typed (+ - @ ~ < > ( ) " *)
        clean = re.sub(r"[^\w\s]", " ", term or "", flags=re.UNICODE).strip()

        # Split into words and keep only those meeting minimum length
        words = [w for w in clean.split() if len(w) >= self._FT_MIN_WORD_LEN]

        if not words:
            # Nothing left after sanitization — return a no-match condition
            return "0 = 1", []

        # Append prefix wildcard so "form" matches "forms", "format", etc.
        ft_term = " ".join(f"{w}*" for w in words)
        return f"MATCH ({cols}) AGAINST (? IN BOOLEAN MODE)", [ft_term]

    def vector_distance_sql(self, column: str, metric: str) -> str:
        raise NotImplementedError("Vector search is not natively supported on the MySQL backend.")

    def prepare_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            # Serialize to JSON array representation for MySQL JSON column
            import json
            return json.dumps(list(value))
        return value

    def deserialize_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            import json
            if isinstance(value, str):
                try:
                    return [float(x) for x in json.loads(value)]
                except Exception:
                    return []
            elif isinstance(value, list):
                return [float(x) for x in value]
        return value

    def handle_exception(self, e: Exception) -> Exception:
        try:
            import pymysql
        except ImportError:
            return e
        if isinstance(e, pymysql.err.IntegrityError):
            from ..exceptions import ModelError
            err_code = e.args[0] if e.args else None
            err_msg = e.args[1] if len(e.args) > 1 else ""
            if err_code == 1062:
                import re
                m = re.search(r"for key '.*?\.(\w+)'", err_msg)
                if not m:
                    m = re.search(r"for key '(\w+)'", err_msg)
                field = m.group(1) if m else "field"
                return ModelError(f"{field} already exists", field=field, original=e)
            elif err_code in (1048, 1364):
                import re
                m = re.search(r"Column '(\w+)' cannot be null", err_msg)
                field = m.group(1) if m else "field"
                return ModelError(f"{field} is required", field=field, original=e)
            return ModelError(err_msg, original=e)
        return e

    def post_create_table(self, model_class: Any) -> None:
        # Create FULLTEXT index for full-text search
        if model_class._search_fields:
            cols = ", ".join([self.quote_identifier(c) for c in model_class._search_fields])
            index_name = f"idx_{model_class._table}_fts"
            try:
                self.execute(f"ALTER TABLE {self.quote_identifier(model_class._table)} ADD FULLTEXT INDEX {self.quote_identifier(index_name)} ({cols});")
            except Exception as e:
                logger.warning("Failed to create FULLTEXT search index for %s: %s", model_class._table, e)

    def transaction(self) -> Any:
        return MySQLTransaction(self.get_connection())

    @property
    def primary_key_def(self) -> str:
        return "id INT AUTO_INCREMENT PRIMARY KEY"

    @property
    def lastrowid_query(self) -> str | None:
        return "SELECT LAST_INSERT_ID() AS id;"
