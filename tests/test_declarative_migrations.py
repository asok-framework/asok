from __future__ import annotations

import unittest
from unittest.mock import patch

from asok import operations
from asok.orm.engines.sqlite import SQLiteEngine
from asok.orm.migrations.autodetector import MigrationAutodetector
from asok.orm.migrations.schema import SQLiteSchemaEditor
from asok.orm.migrations.state import ProjectState, VirtualModelState


class TestDeclarativeMigrations(unittest.TestCase):
    def test_project_state_cloning(self):
        state = ProjectState()
        v_model = VirtualModelState(
            name="TestUser",
            table="test_users",
            fields={"id": {"type": "IntegerField", "sql_type": "INTEGER"}},
        )
        state.models["TestUser"] = v_model

        cloned = state.clone()
        self.assertIn("TestUser", cloned.models)
        self.assertEqual(cloned.models["TestUser"].table, "test_users")

        # Mutate clone and assert original is unchanged
        cloned.models["TestUser"].fields["email"] = {"type": "StringField"}
        self.assertNotIn("email", state.models["TestUser"].fields)

    def test_autodetect_create_model(self):
        hist = ProjectState()
        curr = ProjectState(
            {
                "Address": VirtualModelState(
                    name="Address",
                    table="addresses",
                    fields={"id": {"type": "IntegerField", "sql_type": "INTEGER"}},
                )
            }
        )

        detector = MigrationAutodetector(hist, curr)
        ops = detector.changes()

        self.assertEqual(len(ops), 1)
        self.assertIsInstance(ops[0], operations.CreateModel)
        self.assertEqual(ops[0].name, "Address")
        self.assertEqual(ops[0].table, "addresses")

    def test_autodetect_add_field(self):
        hist = ProjectState(
            {
                "Address": VirtualModelState(
                    name="Address",
                    table="addresses",
                    fields={"id": {"type": "IntegerField", "sql_type": "INTEGER"}},
                )
            }
        )
        curr = ProjectState(
            {
                "Address": VirtualModelState(
                    name="Address",
                    table="addresses",
                    fields={
                        "id": {"type": "IntegerField", "sql_type": "INTEGER"},
                        "street": {
                            "type": "StringField",
                            "sql_type": "TEXT",
                            "nullable": False,
                        },
                    },
                )
            }
        )

        detector = MigrationAutodetector(hist, curr)
        ops = detector.changes()

        self.assertEqual(len(ops), 1)
        self.assertIsInstance(ops[0], operations.AddField)
        self.assertEqual(ops[0].model_name, "Address")
        self.assertEqual(ops[0].name, "street")
        self.assertEqual(ops[0].field["nullable"], False)

    def test_autodetect_alter_field(self):
        hist = ProjectState(
            {
                "Address": VirtualModelState(
                    name="Address",
                    table="addresses",
                    fields={
                        "price": {
                            "type": "FloatField",
                            "sql_type": "REAL",
                            "default": None,
                        }
                    },
                )
            }
        )
        curr = ProjectState(
            {
                "Address": VirtualModelState(
                    name="Address",
                    table="addresses",
                    fields={
                        "price": {
                            "type": "FloatField",
                            "sql_type": "REAL",
                            "default": 0.0,
                        }
                    },
                )
            }
        )

        detector = MigrationAutodetector(hist, curr)
        ops = detector.changes()

        self.assertEqual(len(ops), 1)
        self.assertIsInstance(ops[0], operations.AlterField)
        self.assertEqual(ops[0].name, "price")
        self.assertEqual(ops[0].old_field["default"], None)
        self.assertEqual(ops[0].new_field["default"], 0.0)

    @patch("builtins.input", return_value="y")
    @patch("sys.stdout.isatty", return_value=True)
    def test_autodetect_rename_field(self, mock_isatty, mock_input):
        hist = ProjectState(
            {
                "User": VirtualModelState(
                    name="User",
                    table="users",
                    fields={"name": {"type": "StringField"}},
                )
            }
        )
        curr = ProjectState(
            {
                "User": VirtualModelState(
                    name="User",
                    table="users",
                    fields={"nom": {"type": "StringField"}},
                )
            }
        )

        detector = MigrationAutodetector(hist, curr)
        ops = detector.changes()

        self.assertEqual(len(ops), 1)
        self.assertIsInstance(ops[0], operations.RenameField)
        self.assertEqual(ops[0].old_name, "name")
        self.assertEqual(ops[0].new_name, "nom")

    def test_sqlite_schema_editor_create_table(self):
        engine = SQLiteEngine(":memory:")
        from asok.cli.database import MigrationConnectionWrapper

        conn = MigrationConnectionWrapper(engine)
        editor = SQLiteSchemaEditor(conn, engine)

        op = operations.CreateModel(
            name="Product",
            table="products",
            fields={"title": {"type": "String", "sql_type": "TEXT", "nullable": False}},
            search_fields=["title"],
        )

        editor.create_table(op)

        # Direct database schema verification
        db_conn = engine.get_connection()
        cursor = db_conn.cursor()

        # Check base table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        )
        self.assertEqual(len(cursor.fetchall()), 1)

        # Check FTS virtual table
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products_fts'"
        )
        self.assertEqual(len(cursor.fetchall()), 1)

        # Check triggers
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='products_ai'"
        )
        self.assertEqual(len(cursor.fetchall()), 1)

    def test_state_forwards_missing_model_does_not_crash(self):
        state = ProjectState()
        # AddField on a missing model state should dynamically create the virtual model state without KeyError
        op = operations.AddField(
            "ContactMessage", "subject", {"type": "StringField", "sql_type": "TEXT"}
        )
        op.state_forwards(state)
        self.assertIn("ContactMessage", state.models)
        self.assertIn("subject", state.models["ContactMessage"].fields)

        # AlterField on an existing model with case mismatch
        op_alter = operations.AlterField(
            "contactmessage",
            "subject",
            {"type": "StringField", "sql_type": "TEXT"},
            {"type": "StringField", "sql_type": "VARCHAR(255)"},
        )
        op_alter.state_forwards(state)
        self.assertEqual(
            state.models["ContactMessage"].fields["subject"]["sql_type"], "VARCHAR(255)"
        )

    def test_infer_model_name_from_table(self):
        from asok.cli.generators import _infer_model_name_from_table

        self.assertEqual(_infer_model_name_from_table("categories"), "Category")
        self.assertEqual(_infer_model_name_from_table("addresses"), "Address")
        self.assertEqual(
            _infer_model_name_from_table("contact_messages"), "ContactMessage"
        )
        self.assertEqual(_infer_model_name_from_table("users"), "User")

    def test_quote_identifier_sanitization(self):
        engine = SQLiteEngine(":memory:")
        self.assertEqual(engine.quote_identifier('"name"'), '"name"')
        self.assertEqual(engine.quote_identifier("name"), '"name"')
        self.assertEqual(engine.quote_identifier("`name`"), '"name"')

    @patch(
        "os.listdir", return_value=["0002_rename.py", "0001_initial.py", "0003_add.py"]
    )
    def test_existing_migration_files_sorted(self, mock_listdir):
        from asok.cli.generators import _existing_migration_files

        files = _existing_migration_files("/fake/dir")
        self.assertEqual(files, ["0001_initial.py", "0002_rename.py", "0003_add.py"])
