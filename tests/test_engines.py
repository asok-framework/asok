from __future__ import annotations

from asok.orm import Field
from asok.orm.engines import get_engine
from asok.orm.engines.mysql import MySQLEngine
from asok.orm.engines.postgres import PostgresEngine
from asok.orm.engines.sqlite import SQLiteEngine
from asok.orm.exceptions import ModelError


def test_get_engine():
    sqlite_engine = get_engine("sqlite://db.sqlite3")
    assert isinstance(sqlite_engine, SQLiteEngine)
    assert sqlite_engine.db_path == "db.sqlite3"

    sqlite_engine2 = get_engine("db.sqlite3")
    assert isinstance(sqlite_engine2, SQLiteEngine)

    postgres_engine = get_engine("postgresql://user:pass@localhost/db")
    assert isinstance(postgres_engine, PostgresEngine)
    assert postgres_engine.dsn == "postgresql://user:pass@localhost/db"

    mysql_engine = get_engine("mysql://user:pass@localhost/db")
    assert isinstance(mysql_engine, MySQLEngine)
    assert mysql_engine.dsn == "mysql://user:pass@localhost/db"


def test_engine_quoting():
    sqlite = SQLiteEngine("db.sqlite3")
    postgres = PostgresEngine("postgresql://...")
    mysql = MySQLEngine("mysql://...")

    assert sqlite.quote_identifier("users") == '"users"'
    assert postgres.quote_identifier("users") == '"users"'
    assert mysql.quote_identifier("users") == "`users`"


def test_engine_query_translation():
    sqlite = SQLiteEngine("db.sqlite3")
    postgres = PostgresEngine("postgresql://...")
    mysql = MySQLEngine("mysql://...")

    sql = "SELECT * FROM users WHERE email = ? AND active = ?"
    args = ["test@example.com", True]

    sql_sqlite, args_sqlite = sqlite.translate_query(sql, args)
    assert sql_sqlite == "SELECT * FROM users WHERE email = ? AND active = ?"
    assert args_sqlite == args

    sql_pg, args_pg = postgres.translate_query(sql, args)
    assert sql_pg == "SELECT * FROM users WHERE email = %s AND active = %s"
    assert args_pg == args

    sql_my, args_my = mysql.translate_query(sql, args)
    assert sql_my == "SELECT * FROM users WHERE email = %s AND active = %s"
    assert args_my == args


def test_engine_column_types():
    postgres = PostgresEngine("postgresql://...")
    mysql = MySQLEngine("mysql://...")

    # String field
    str_field = Field.String(max_length=150)
    assert postgres.get_column_type(str_field) == "VARCHAR(150)"
    assert mysql.get_column_type(str_field) == "VARCHAR(150)"

    # Text field
    text_field = Field.Text()
    assert postgres.get_column_type(text_field) == "TEXT"
    assert mysql.get_column_type(text_field) == "TEXT"

    # Boolean field
    bool_field = Field.Boolean()
    assert postgres.get_column_type(bool_field) == "BOOLEAN"
    assert mysql.get_column_type(bool_field) == "TINYINT(1)"

    # JSON field
    json_field = Field.JSON()
    assert postgres.get_column_type(json_field) == "JSONB"
    assert mysql.get_column_type(json_field) == "JSON"

    # Vector field
    vector_field = Field.Vector(dimensions=512)
    assert postgres.get_column_type(vector_field) == "vector(512)"
    assert mysql.get_column_type(vector_field) == "JSON"


def test_sqlite_exception_translation():
    import sqlite3

    sqlite = SQLiteEngine("db.sqlite3")

    # Test Unique constraint failed error
    exc = sqlite3.IntegrityError("UNIQUE constraint failed: users.email")
    translated = sqlite.handle_exception(exc)
    assert isinstance(translated, ModelError)
    assert "email already exists" in str(translated)
    assert translated.field == "email"

    # Test NOT NULL constraint failed error
    exc = sqlite3.IntegrityError("NOT NULL constraint failed: users.username")
    translated = sqlite.handle_exception(exc)
    assert isinstance(translated, ModelError)
    assert "username is required" in str(translated)
    assert translated.field == "username"
