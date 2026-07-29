"""Regression: adding a NOT NULL column (no default) to a populated table used to
fail with SQLite's "Cannot add a NOT NULL column with default value NULL".

add_column now backfills a type-appropriate default (0 for numeric/FK, '' for
text) so the migration applies; the value should be reviewed afterwards.
"""

import pytest

from asok import Asok, Field, Model
from asok.orm import MODELS_REGISTRY
from asok.orm.migrations.schema import SQLiteSchemaEditor


class Note(Model):
    __tablename__ = "notes_mig"
    title = Field.String()


class _Editor(SQLiteSchemaEditor):
    def __init__(self, engine):
        self.engine = engine

    def execute(self, sql, params=None):
        return self.engine.execute(sql, params or ())


@pytest.fixture
def engine():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["Note"] = Note
    eng = Note.get_engine()
    eng.execute("DROP TABLE IF EXISTS notes_mig")
    Note.create_table()
    eng.execute("INSERT INTO notes_mig (title) VALUES ('existing')")  # populated
    return eng


def test_add_notnull_int_column_backfills_zero(engine):
    _Editor(engine).add_column(
        "Note",
        "author_id",
        {
            "sql_type": "INTEGER",
            "nullable": False,
            "default": None,
            "is_foreign_key": True,
        },
    )
    rows = engine.execute("SELECT author_id FROM notes_mig")
    assert rows[0]["author_id"] == 0


def test_add_notnull_text_column_backfills_empty(engine):
    _Editor(engine).add_column(
        "Note",
        "slug",
        {"sql_type": "TEXT", "nullable": False, "default": None},
    )
    rows = engine.execute("SELECT slug FROM notes_mig")
    assert rows[0]["slug"] == ""


def test_explicit_default_is_respected(engine):
    _Editor(engine).add_column(
        "Note",
        "views",
        {"sql_type": "INTEGER", "nullable": False, "default": 5},
    )
    rows = engine.execute("SELECT views FROM notes_mig")
    assert rows[0]["views"] == 5


def test_nullable_column_stays_null(engine):
    _Editor(engine).add_column(
        "Note",
        "subtitle",
        {"sql_type": "TEXT", "nullable": True, "default": None},
    )
    rows = engine.execute("SELECT subtitle FROM notes_mig")
    assert rows[0]["subtitle"] is None
