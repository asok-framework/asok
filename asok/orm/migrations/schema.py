from __future__ import annotations

import re
from typing import Any, Dict

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

    def _get_table_name(self, model_name: str) -> str:
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
        return "'" + str(default).replace("'", "''") + "'"

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
        self, model_name: str, col_name: str, field_data: Dict[str, Any]
    ) -> None:
        table = self._get_table_name(model_name)
        q_table = self.engine.quote_identifier(table)
        col_sql = self._build_column_sql(col_name, field_data)
        self.execute(f"ALTER TABLE {q_table} ADD COLUMN {col_sql}")

    def remove_column(self, model_name: str, col_name: str) -> None:
        table = self._get_table_name(model_name)
        q_table = self.engine.quote_identifier(table)
        q_col = self.engine.quote_identifier(col_name)
        self.execute(f"ALTER TABLE {q_table} DROP COLUMN {q_col}")

    def rename_column(self, model_name: str, old_name: str, new_name: str) -> None:
        table = self._get_table_name(model_name)
        q_table = self.engine.quote_identifier(table)
        q_old = self.engine.quote_identifier(old_name)
        q_new = self.engine.quote_identifier(new_name)
        self.execute(f"ALTER TABLE {q_table} RENAME COLUMN {q_old} TO {q_new}")

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
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
    ) -> None:
        table = self._get_table_name(model_name)
        q_table = self.engine.quote_identifier(table)
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
    ) -> None:
        table = self._get_table_name(model_name)
        q_table = self.engine.quote_identifier(table)
        col_sql = self._build_column_sql(col_name, new_field)
        self.execute(f"ALTER TABLE {q_table} MODIFY COLUMN {col_sql}")


class SQLiteSchemaEditor(BaseSchemaEditor):
    """SQLite SQL Schema Compiler."""

    def delete_table_by_name(self, table: str) -> None:
        fts_table = f"{table}_fts"
        self.execute(f'DROP TRIGGER IF EXISTS "{table}_ai"')
        self.execute(f'DROP TRIGGER IF EXISTS "{table}_ad"')
        self.execute(f'DROP TRIGGER IF EXISTS "{table}_au"')
        self.execute(f'DROP TABLE IF EXISTS "{fts_table}"')
        super().delete_table_by_name(table)

    def alter_column(
        self,
        model_name: str,
        col_name: str,
        old_field: Dict[str, Any],
        new_field: Dict[str, Any],
    ) -> None:
        # SQLite doesn't support easy ALTER COLUMN. We output warning and proceed.
        import logging

        logger = logging.getLogger("asok.orm")
        logger.warning(
            "SQLite does not support altering columns directly. "
            "Please recreate table manually if DB-level constraint update is required."
        )
