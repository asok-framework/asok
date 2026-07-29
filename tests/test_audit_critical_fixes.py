import os
import sqlite3
from typing import Any

from asok.orm import Field, Model
from asok.orm.migrations.autodetector import MigrationAutodetector
from asok.orm.migrations.operations import AddField, RenameField
from asok.orm.migrations.schema import BaseSchemaEditor, SQLiteSchemaEditor
from asok.orm.migrations.state import ProjectState, VirtualModelState
from asok.request import Request


class MockEngine:
    primary_key_def = "id INTEGER PRIMARY KEY AUTOINCREMENT"

    def quote_identifier(self, name: str) -> str:
        return f'"{name}"'

    def get_column_type(self, field: Any) -> str:
        return field.sql_type


class MockConnection:
    def __init__(self, sqlite_conn):
        self.conn = sqlite_conn

    def execute(self, sql: str, params: tuple = ()):
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        if cursor.description:
            return [dict(r) for r in cursor.fetchall()]
        return cursor.rowcount


def test_request_ip_forwarded_for_first_client():
    env = {
        "REMOTE_ADDR": "127.0.0.1",
        "HTTP_X_FORWARDED_FOR": "203.0.113.195, 70.41.3.18, 150.172.238.178",
    }
    req = Request(env)
    # Without trusted proxy configured
    assert req.ip == "127.0.0.1"

    # With trusted proxy
    class DummyApp:
        config = {"TRUSTED_PROXIES": "*"}

    env["asok.app"] = DummyApp()
    req_trusted = Request(env)
    assert req_trusted.ip == "203.0.113.195"


def test_schema_editor_custom_table_name():
    class DummyConn:
        def __init__(self):
            self.sqls = []

        def execute(self, sql, params=()):
            self.sqls.append(sql)

    conn = DummyConn()
    engine = MockEngine()
    editor = BaseSchemaEditor(conn, engine)

    # Test operation with explicit table name
    op_add = AddField(
        "CustomModel",
        "bio",
        {"sql_type": "TEXT", "nullable": True},
        table="custom_accounts",
    )
    op_add.database_forwards(editor)

    assert 'ALTER TABLE "custom_accounts" ADD COLUMN "bio" TEXT' in conn.sqls[0]


def test_schema_editor_format_default_value_sql_functions():
    editor = BaseSchemaEditor(None, MockEngine())
    assert editor._format_default_value("CURRENT_TIMESTAMP") == "CURRENT_TIMESTAMP"
    assert editor._format_default_value("NOW()") == "NOW()"
    assert editor._format_default_value("hello") == "'hello'"
    assert editor._format_default_value(42) == "42"
    assert editor._format_default_value(True) == "true"


def test_sqlite_alter_column_table_rebuild():
    raw_conn = sqlite3.connect(":memory:")
    raw_conn.row_factory = sqlite3.Row
    raw_conn.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, age INTEGER);"
    )
    raw_conn.execute("INSERT INTO users (name, age) VALUES ('Alice', 30);")

    conn = MockConnection(raw_conn)
    engine = MockEngine()
    editor = SQLiteSchemaEditor(conn, engine)

    old_field = {"sql_type": "INTEGER", "nullable": True}
    new_field = {"sql_type": "TEXT", "nullable": True}

    editor.alter_column("User", "age", old_field, new_field, table="users")

    # Verify column type was changed and data retained
    cursor = raw_conn.cursor()
    cursor.execute("PRAGMA table_info(users);")
    cols = {r[1]: r[2] for r in cursor.fetchall()}
    assert cols["age"] == "TEXT"

    cursor.execute("SELECT name, age FROM users;")
    row = cursor.fetchone()
    assert row[0] == "Alice"
    assert str(row[1]) == "30"


def test_autodetector_env_auto_rename():
    hist_model = VirtualModelState("User", "users", {"first_name": {"type": "String"}})
    curr_model = VirtualModelState("User", "users", {"given_name": {"type": "String"}})
    hist_state = ProjectState({"User": hist_model})
    curr_state = ProjectState({"User": curr_model})

    autodetector = MigrationAutodetector(hist_state, curr_state)

    try:
        os.environ["ASOK_AUTO_RENAME"] = "1"
        ops = autodetector.changes()
        assert len(ops) == 1
        assert isinstance(ops[0], RenameField)
        assert ops[0].old_name == "first_name"
        assert ops[0].new_name == "given_name"
        assert ops[0].table == "users"
    finally:
        os.environ.pop("ASOK_AUTO_RENAME", None)


def test_query_where_contains():
    class DummyUser(Model):
        name = Field.String()

    q = DummyUser.query().where_contains("name", "100%_user\\test")
    sql = q.to_sql()
    assert "WHERE name LIKE ?" in sql
    assert q._args[0] == "%100\\%\\_user\\\\test%"
