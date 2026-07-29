from __future__ import annotations

import re
from typing import Any, Dict, Optional

from .operations import CreateModel


class DummyField:
    def __init__(self, field_data: Dict[str, Any]):
        self.sql_type = field_data.get("sql_type", "TEXT")
        self.nullable = field_data.get("nullable", True)
        self.default = field_data.get("default", None)
        self.unique = field_data.get("unique", False)
        self.max_length = field_data.get("max_length", None)
        self.precision = field_data.get("precision", None)
        self.is_boolean = field_data.get("is_boolean", False)
        self.is_json = field_data.get("is_json", False)
        self.is_uuid = field_data.get("is_uuid", False)
        self.is_datetime = field_data.get("is_datetime", False)
        self.is_date = field_data.get("is_date", False)
        self.is_time = field_data.get("is_time", False)
        self.is_vector = field_data.get("is_vector", False)
        self.dimensions = field_data.get("dimensions", None)
        self.is_foreign_key = field_data.get("type") == "ForeignKey" or field_data.get(
            "is_foreign_key", False
        )
        self.is_decimal = field_data.get("type") == "Decimal" or field_data.get(
            "is_decimal", False
        )


class DummyModelClass:
    def __init__(self, op: CreateModel):
        self._table = op.table
        self._search_fields = op.search_fields
        self._fields = {
            f_name: DummyField(f_data) for f_name, f_data in op.fields.items()
        }

    def _valid_column(self, name: str) -> None:
        if name not in self._fields:
            raise ValueError(f"Invalid column: {name}")


class BaseSchemaEditor:
    """Base class for translating declarations to engine-specific SQL queries."""

    def __init__(self, connection: Any, engine: Any):
        self.connection = connection
        self.engine = engine

    def execute(self, sql: str, params: Any = None) -> None:
        self.connection.execute(sql, params or ())

    def _get_table_name(self, model_name: str, table: Optional[str] = None) -> str:
        if table:
            return table

        from .. import MODELS_REGISTRY

        if model_name in MODELS_REGISTRY:
            return MODELS_REGISTRY[model_name]._table

        # Fallback to standard convention (snake_case + pluralize)
        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", model_name)
        snake = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()
        if snake.endswith("y"):
            return snake[:-1] + "ies"
        if snake.endswith("s"):
            return snake
        return snake + "s"

    def _build_column_sql(
        self, name: str, field_data: Dict[str, Any], include_constraints: bool = True
    ) -> str:
        dummy = DummyField(field_data)
        col_type = self.engine.get_column_type(dummy)
        q_name = self.engine.quote_identifier(name)
        col_sql = f"{q_name} {col_type}"

        if include_constraints:
            if field_data.get("unique"):
                col_sql += " UNIQUE"
            if not field_data.get("nullable"):
                col_sql += " NOT NULL"

        default = field_data.get("default")
        if default is not None:
            col_sql += f" DEFAULT {self._format_default_value(default)}"
        return col_sql

    def _format_default_value(self, default: Any) -> str:
        if isinstance(default, bool):
            return str(default).lower()
        if isinstance(default, (int, float)):
            return str(default)
        str_val = str(default)
        upper_val = str_val.strip().upper()
        if upper_val in (
            "CURRENT_TIMESTAMP",
            "CURRENT_DATE",
            "CURRENT_TIME",
            "NOW()",
            "NULL",
            "TRUE",
            "FALSE",
        ):
            return upper_val
        return "'" + str_val.replace("'", "''") + "'"

    def create_table(self, operation: CreateModel) -> None:
        q_table = self.engine.quote_identifier(operation.table)
        pk_def = getattr(
            self.engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )

        fields_sql = []
        if "id" not in operation.fields:
            fields_sql.append(pk_def)

        for f_name, f_data in operation.fields.items():
            if f_name == "id":
                fields_sql.append(pk_def)
                continue
            fields_sql.append(self._build_column_sql(f_name, f_data))

        sql = f"CREATE TABLE IF NOT EXISTS {q_table} ({', '.join(fields_sql)})"
        self.execute(sql)
        self.engine.post_create_table(DummyModelClass(operation))

    def delete_table_by_name(self, table: str) -> None:
        q_table = self.engine.quote_identifier(table)
        self.execute(f"DROP TABLE IF EXISTS {q_table}")

    def add_column(
        self,
        model_name: str,
        col_name: str,
        field_data: Dict[str, Any],
        table: Optional[str] = None,
    ) -> None:
        tbl = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(tbl)
        field_data = self._backfill_add_column_default(col_name, field_data)
        col_sql = self._build_column_sql(col_name, field_data)
        self.execute(f"ALTER TABLE {q_table} ADD COLUMN {col_sql}")

    def _backfill_add_column_default(
        self, col_name: str, field_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Give a NOT NULL column a safe DEFAULT when added to a populated table.

        SQLite/Postgres reject `ALTER TABLE ADD COLUMN ... NOT NULL` without a
        default (existing rows would be NULL). Backfill 0 for numeric/boolean
        columns, '' otherwise, so the migration applies. Review the backfilled
        rows afterwards — e.g. a foreign key gets 0 and may need fixing.
        """
        if field_data.get("nullable") or field_data.get("default") is not None:
            return field_data
        col_type = self.engine.get_column_type(DummyField(field_data)).upper()
        numeric = ("INT", "REAL", "NUMERIC", "DECIMAL", "DOUBLE", "FLOAT", "BOOL")
        fallback: Any = 0 if any(t in col_type for t in numeric) else ""
        import logging

        logging.getLogger("asok.migrations").warning(
            "Column '%s' is NOT NULL without a default; backfilling existing rows "
            "with %r so the migration can apply. Review this data.",
            col_name,
            fallback,
        )
        return {**field_data, "default": fallback}

    def remove_column(
        self, model_name: str, col_name: str, table: Optional[str] = None
    ) -> None:
        tbl = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(tbl)
        q_col = self.engine.quote_identifier(col_name)
        self.execute(f"ALTER TABLE {q_table} DROP COLUMN {q_col}")

    def rename_column(
        self,
        model_name: str,
        old_name: str,
        new_name: str,
        table: Optional[str] = None,
    ) -> None:
        tbl = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(tbl)
        q_old = self.engine.quote_identifier(old_name)
        q_new = self.engine.quote_identifier(new_name)
        self.execute(f"ALTER TABLE {q_table} RENAME COLUMN {q_old} TO {q_new}")

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        table: Optional[str] = None,
    ) -> None:
        raise NotImplementedError()


class PostgresSchemaEditor(BaseSchemaEditor):
    """PostgreSQL SQL Schema Compiler."""

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        table: Optional[str] = None,
    ) -> None:
        tbl = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(tbl)
        q_col = self.engine.quote_identifier(col_name)

        clauses: list[str] = []
        self._collect_type_alteration(q_col, old_field, new_field, clauses)
        self._collect_nullability_alteration(q_col, old_field, new_field, clauses)
        self._collect_default_alteration(q_col, old_field, new_field, clauses)

        if clauses:
            self.execute(f"ALTER TABLE {q_table} {', '.join(clauses)}")

    def _collect_type_alteration(
        self,
        q_col: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        clauses: list[str],
    ) -> None:
        if old_field.get("sql_type") != new_field.get("sql_type") or old_field.get(
            "type"
        ) != new_field.get("type"):
            dummy = DummyField(new_field)
            col_type = self.engine.get_column_type(dummy)
            clauses.append(
                f"ALTER COLUMN {q_col} TYPE {col_type} USING {q_col}::{col_type}"
            )

    def _collect_nullability_alteration(
        self,
        q_col: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        clauses: list[str],
    ) -> None:
        if old_field.get("nullable") != new_field.get("nullable"):
            if new_field.get("nullable"):
                clauses.append(f"ALTER COLUMN {q_col} DROP NOT NULL")
            else:
                clauses.append(f"ALTER COLUMN {q_col} SET NOT NULL")

    def _collect_default_alteration(
        self,
        q_col: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        clauses: list[str],
    ) -> None:
        if old_field.get("default") != new_field.get("default"):
            default = new_field.get("default")
            if default is None:
                clauses.append(f"ALTER COLUMN {q_col} DROP DEFAULT")
            else:
                clauses.append(
                    f"ALTER COLUMN {q_col} SET DEFAULT {self._format_default_value(default)}"
                )


class MySQLSchemaEditor(BaseSchemaEditor):
    """MySQL SQL Schema Compiler."""

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        table: Optional[str] = None,
    ) -> None:
        tbl = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(tbl)
        col_sql = self._build_column_sql(col_name, new_field)
        self.execute(f"ALTER TABLE {q_table} MODIFY COLUMN {col_sql}")


class SQLiteSchemaEditor(BaseSchemaEditor):
    """SQLite SQL Schema Compiler."""

    def delete_table_by_name(self, table: str) -> None:
        q_ai = self.engine.quote_identifier(f"{table}_ai")
        q_ad = self.engine.quote_identifier(f"{table}_ad")
        q_au = self.engine.quote_identifier(f"{table}_au")
        q_fts = self.engine.quote_identifier(f"{table}_fts")
        self.execute(f"DROP TRIGGER IF EXISTS {q_ai}")
        self.execute(f"DROP TRIGGER IF EXISTS {q_ad}")
        self.execute(f"DROP TRIGGER IF EXISTS {q_au}")
        self.execute(f"DROP TABLE IF EXISTS {q_fts}")
        super().delete_table_by_name(table)

    def _get_sqlite_table_columns(self, q_table: str) -> Optional[list]:
        try:
            return self.connection.execute(f"PRAGMA table_info({q_table})")
        except Exception:
            return None

    def _extract_column_info(self, c: Any) -> tuple[str, str, bool, Any]:
        if isinstance(c, dict):
            return c["name"], c["type"], c["notnull"] == 1, c["dflt_value"]
        return c[1], c[2], c[3] == 1, c[4]

    def _build_existing_sqlite_col_sql(
        self, c_name: str, c_type: str, c_notnull: bool, c_dflt: Any
    ) -> str:
        col_def = f"{self.engine.quote_identifier(c_name)} {c_type}"
        if c_notnull:
            col_def += " NOT NULL"
        if c_dflt is not None:
            col_def += f" DEFAULT {c_dflt}"
        return col_def

    def _build_sqlite_col_sql(
        self, c: Any, col_name: str, new_field: Dict[str, Any], pk_def: str
    ) -> str:
        c_name, c_type, c_notnull, c_dflt = self._extract_column_info(c)
        if c_name == "id":
            return pk_def
        if c_name == col_name:
            return self._build_column_sql(c_name, new_field)
        return self._build_existing_sqlite_col_sql(c_name, c_type, c_notnull, c_dflt)

    def _rebuild_sqlite_table(
        self,
        q_table: str,
        table_name: str,
        columns: list,
        col_names: list,
        col_name: str,
        new_field: Dict[str, Any],
    ) -> None:
        temp_table = f"__temp_{table_name}"
        q_temp = self.engine.quote_identifier(temp_table)
        pk_def = getattr(
            self.engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        column_sqls = [
            self._build_sqlite_col_sql(c, col_name, new_field, pk_def) for c in columns
        ]
        create_temp_sql = f"CREATE TABLE {q_temp} ({', '.join(column_sqls)})"
        cols_joined = ", ".join(self.engine.quote_identifier(c) for c in col_names)
        insert_sql = (
            f"INSERT INTO {q_temp} ({cols_joined}) SELECT {cols_joined} FROM {q_table}"
        )
        self.execute(create_temp_sql)
        self.execute(insert_sql)
        self.execute(f"DROP TABLE {q_table}")
        self.execute(f"ALTER TABLE {q_temp} RENAME TO {q_table}")

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
        table: Optional[str] = None,
    ) -> None:
        table_name = self._get_table_name(model_name, table)
        q_table = self.engine.quote_identifier(table_name)
        columns = self._get_sqlite_table_columns(q_table)
        if not columns:
            return
        col_names = [
            col["name"] if isinstance(col, dict) else col[1] for col in columns
        ]
        if col_name not in col_names:
            return
        self._rebuild_sqlite_table(
            q_table, table_name, columns, col_names, col_name, new_field
        )
