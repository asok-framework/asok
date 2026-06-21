from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Tuple
from urllib.parse import urlparse

from .base import BaseEngine

logger = logging.getLogger("asok.orm")


class MySQLTransaction:
    """Transaction context manager for MySQL with nested transaction (SAVEPOINT) support."""

    def __init__(self, engine: MySQLEngine):
        self.engine = engine
        self.conn = engine.get_connection()
        self.sp_name = None

    def __enter__(self) -> MySQLTransaction:
        if not hasattr(self.engine._local, "txn_level"):
            self.engine._local.txn_level = 0
        self.engine._local.txn_level += 1
        level = self.engine._local.txn_level
        if level == 1:
            self.conn.begin()
        else:
            self.sp_name = f"sp_{level}"
            with self.conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {self.sp_name}")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        level = self.engine._local.txn_level
        self.engine._local.txn_level -= 1
        if exc_type is not None:
            if level == 1:
                self.conn.rollback()
            else:
                with self.conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO {self.sp_name}")
                    cur.execute(f"RELEASE SAVEPOINT {self.sp_name}")
        else:
            if level == 1:
                self.conn.commit()
            else:
                with self.conn.cursor() as cur:
                    cur.execute(f"RELEASE SAVEPOINT {self.sp_name}")


class MySQLEngine(BaseEngine):
    """MySQL engine backend using the pymysql library."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._local = threading.local()

    def _import_pymysql(self) -> Any:
        try:
            import pymysql
            import pymysql.cursors
            return pymysql
        except ImportError:
            raise ImportError(
                "MySQL support requires 'pymysql'.\n"
                'Please install it using: pip install "asok[mysql]"'
            )

    def _parse_mysql_dsn(self) -> tuple[str, int, str, str, str]:
        parsed = urlparse(self.dsn)
        host = parsed.hostname or "localhost"
        port = parsed.port or 3306
        user = parsed.username or "root"
        password = parsed.password or ""
        db = parsed.path.lstrip("/")
        return host, port, user, password, db

    def _track_connection_mysql(self, conn: Any) -> None:
        self._local.conn = conn
        if not hasattr(self._local, "_all_conns"):
            self._local._all_conns = []
        self._local._all_conns.append(conn)

    def get_connection(self) -> Any:
        conn = getattr(self._local, "conn", None)
        if conn is not None and conn.open:
            return conn

        pymysql = self._import_pymysql()
        host, port, user, password, db = self._parse_mysql_dsn()

        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
        self._track_connection_mysql(conn)
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

    def translate_query(
        self, sql: str, args: List[Any] | Tuple[Any, ...] | None = None
    ) -> Tuple[str, List[Any]]:
        # Translate ? to %s
        translated_sql = sql.replace("?", "%s")
        translated_sql = self._translate_autoincrement(translated_sql)
        translated_sql = self._translate_sqlite_fts(translated_sql)
        return translated_sql, list(args) if args else []

    def _translate_autoincrement(self, sql: str) -> str:
        import re
        if "AUTOINCREMENT" not in sql:
            return sql
        sql = re.sub(
            r'INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            'INT AUTO_INCREMENT PRIMARY KEY',
            sql,
            flags=re.IGNORECASE
        )
        return re.sub(
            r'id\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT',
            'id INT AUTO_INCREMENT PRIMARY KEY',
            sql,
            flags=re.IGNORECASE
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
        import re
        match = re.search(
            r'CREATE\s+VIRTUAL\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:"?(\w+)"?)\s+USING\s+fts5\((.*?)\)',
            sql,
            re.IGNORECASE | re.DOTALL
        )
        if not match:
            return "SELECT 1"
        return self._do_build_fts(match.group(1), match.group(2))

    def _do_build_fts(self, fts_table: str, params_str: str) -> str:
        import re
        target_table = fts_table[:-4] if fts_table.endswith("_fts") else fts_table
        content_match = re.search(r"content\s*=\s*'([^']+)'", params_str)
        if content_match:
            target_table = content_match.group(1)
        cols = self._extract_fts_cols(params_str)
        if not cols:
            return "SELECT 1"
        idx_cols = ", ".join(f"`{c}`" for c in cols)
        idx_name = f"idx_{target_table}_fts"
        return f'ALTER TABLE `{target_table}` ADD FULLTEXT INDEX `{idx_name}` ({idx_cols})'

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


    def _detect_mysql_type_attrs(self, field: Any) -> str | None:
        """Check field type attributes and return MySQL type, or None for standard mapping."""
        mappings = (
            ("is_boolean", "TINYINT(1)"),
            ("is_json", "JSON"),
            ("is_uuid", "VARCHAR(36)"),
            ("is_datetime", "DATETIME"),
            ("is_date", "DATE"),
            ("is_time", "TIME"),
            ("is_vector", "JSON"),
            ("is_foreign_key", "INTEGER"),
        )
        for attr, mysql_type in mappings:
            if getattr(field, attr, False):
                return mysql_type

        if getattr(field, "is_decimal", False):
            return f"DECIMAL({getattr(field, 'precision', 10)}, 2)"
        return None

    def _map_mysql_sql_type(self, field: Any) -> str:
        """Map SQLite sql_type to its MySQL equivalent."""
        sql_type = field.sql_type.upper()
        if sql_type == "TEXT":
            max_len = getattr(field, "max_length", None)
            if max_len:
                return f"VARCHAR({max_len})"
            return "TEXT"

        mappings = {
            "INTEGER": "INT",
            "REAL": "DOUBLE",
            "BLOB": "LONGBLOB",
        }
        return mappings.get(sql_type, sql_type)

    def get_column_type(self, field: Any) -> str:
        result = self._detect_mysql_type_attrs(field)
        return result if result is not None else self._map_mysql_sql_type(field)

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

    def _clean_search_term_mysql(self, term: str) -> str:
        import re
        return re.sub(r"[^\w\s]", " ", term or "", flags=re.UNICODE).strip()

    def _get_search_words_mysql(self, clean: str) -> list[str]:
        return [w for w in clean.split() if len(w) >= self._FT_MIN_WORD_LEN]

    def search_sql(
        self, table: str, columns: List[str], term: str
    ) -> Tuple[str, List[Any]]:
        cols = ", ".join([self.quote_identifier(c) for c in columns])
        clean = self._clean_search_term_mysql(term)
        words = self._get_search_words_mysql(clean)

        if not words:
            # Nothing left after sanitization — return a no-match condition
            return "0 = 1", []

        # Append prefix wildcard so "form" matches "forms", "format", etc.
        ft_term = " ".join(f"{w}*" for w in words)
        return f"MATCH ({cols}) AGAINST (? IN BOOLEAN MODE)", [ft_term]

    def vector_distance_sql(self, column: str, metric: str) -> str:
        raise NotImplementedError(
            "Vector search is not natively supported on the MySQL backend."
        )

    def prepare_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            # Serialize to JSON array representation for MySQL JSON column
            import json

            return json.dumps(list(value))
        return value

    def _deserialize_vector_json_mysql(self, value: str) -> list[float]:
        import json
        try:
            return [float(x) for x in json.loads(value)]
        except Exception:
            return []

    def _deserialize_vector_list_mysql(self, value: list) -> list[float]:
        return [float(x) for x in value]

    def deserialize_value(self, field: Any, value: Any) -> Any:
        if getattr(field, "is_vector", False) and value is not None:
            if isinstance(value, str):
                return self._deserialize_vector_json_mysql(value)
            if isinstance(value, list):
                return self._deserialize_vector_list_mysql(value)
        return value

    def _get_pymysql_module(self) -> Any:
        try:
            import pymysql
            return pymysql
        except ImportError:
            return None

    def _handle_mysql_duplicate_key(self, err_msg: str, e: Any) -> Exception:
        import re

        from ..exceptions import ModelError
        m = re.search(r"for key '.*?\.(\w+)'", err_msg)
        if not m:
            m = re.search(r"for key '(\w+)'", err_msg)
        field = m.group(1) if m else "field"
        return ModelError(f"{field} already exists", field=field, original=e)

    def _handle_mysql_not_null(self, err_msg: str, e: Any) -> Exception:
        import re

        from ..exceptions import ModelError
        m = re.search(r"Column '(\w+)' cannot be null", err_msg)
        field = m.group(1) if m else "field"
        return ModelError(f"{field} is required", field=field, original=e)

    def _get_err_code_msg(self, e: Any) -> tuple[Any, str]:
        err_code = e.args[0] if e.args else None
        err_msg = e.args[1] if len(e.args) > 1 else ""
        return err_code, err_msg

    def handle_exception(self, e: Exception) -> Exception:
        pymysql = self._get_pymysql_module()
        if pymysql is None:
            return e
        if isinstance(e, pymysql.err.IntegrityError):
            err_code, err_msg = self._get_err_code_msg(e)
            if err_code == 1062:
                return self._handle_mysql_duplicate_key(err_msg, e)
            if err_code in (1048, 1364):
                return self._handle_mysql_not_null(err_msg, e)
            from ..exceptions import ModelError
            return ModelError(err_msg, original=e)
        return e

    def post_create_table(self, model_class: Any) -> None:
        # Create FULLTEXT index for full-text search
        if model_class._search_fields:
            cols = ", ".join(
                [self.quote_identifier(c) for c in model_class._search_fields]
            )
            index_name = f"idx_{model_class._table}_fts"
            try:
                self.execute(
                    f"ALTER TABLE {self.quote_identifier(model_class._table)} ADD FULLTEXT INDEX {self.quote_identifier(index_name)} ({cols});"
                )
            except Exception as e:
                logger.warning(
                    "Failed to create FULLTEXT search index for %s: %s",
                    model_class._table,
                    e,
                )

    def transaction(self) -> Any:
        return MySQLTransaction(self)

    @property
    def primary_key_def(self) -> str:
        return "id INT AUTO_INCREMENT PRIMARY KEY"

    @property
    def lastrowid_query(self) -> str | None:
        return "SELECT LAST_INSERT_ID() AS id;"
