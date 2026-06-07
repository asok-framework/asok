import os
import tempfile
from unittest.mock import MagicMock, patch

from asok.cli.database import run_db_command, run_migrate
from asok.orm import Migrations
from asok.orm.engines.sqlite import SQLiteEngine


# Helper to create migration file
def write_migration(path, name, up_sql, down_sql):
    content = f"""
def up(conn):
    conn.execute("{up_sql}")

def down(conn):
    conn.execute("{down_sql}")
"""
    with open(os.path.join(path, f"{name}.py"), "w") as f:
        f.write(content)


def test_cli_migrations_and_introspection():
    orig_cwd = os.getcwd()
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create project structure
            wsgi_file = os.path.join(temp_dir, "wsgi.py")
            with open(wsgi_file, "w") as f:
                f.write("app = None")

            mig_dir = os.path.join(temp_dir, "src", "migrations")
            os.makedirs(mig_dir, exist_ok=True)

            write_migration(
                mig_dir,
                "0001_initial",
                "CREATE TABLE test_users (id INTEGER PRIMARY KEY, name TEXT)",
                "DROP TABLE test_users",
            )
            write_migration(
                mig_dir,
                "0002_add_posts",
                "CREATE TABLE test_posts (id INTEGER PRIMARY KEY, title TEXT)",
                "DROP TABLE test_posts",
            )
            write_migration(
                mig_dir,
                "0003_add_comments",
                "CREATE TABLE test_comments (id INTEGER PRIMARY KEY, body TEXT)",
                "DROP TABLE test_comments",
            )

            # Create a database file in temp_dir
            db_file = os.path.join(temp_dir, "test.db")
            test_engine = SQLiteEngine(db_file)

            # Mock find_project_root and Model.get_engine
            with (
                patch("asok.cli.database._find_project_root", return_value=temp_dir),
                patch("asok.orm.Model.get_engine", return_value=test_engine),
            ):
                # 1. Run migrations forward (normal)
                run_migrate()
                assert test_engine.table_exists("test_users")
                assert test_engine.table_exists("test_posts")
                assert test_engine.table_exists("test_comments")
                applied = Migrations.get_applied(test_engine)
                assert len(applied) == 3
                assert applied == [
                    "0001_initial",
                    "0002_add_posts",
                    "0003_add_comments",
                ]

                # 2. Test status display (should not crash)
                run_migrate(status=True)

                # 3. Rollback with steps = 1 (should rollback 0003_add_comments)
                run_migrate(rollback=True, steps=1)
                assert test_engine.table_exists("test_users")
                assert test_engine.table_exists("test_posts")
                assert not test_engine.table_exists("test_comments")
                assert Migrations.get_applied(test_engine) == [
                    "0001_initial",
                    "0002_add_posts",
                ]

                # 4. Migrate back forward up to 0003_add_comments using --to
                run_migrate(to_migration="0003_add_comments")
                assert test_engine.table_exists("test_comments")
                assert Migrations.get_applied(test_engine) == [
                    "0001_initial",
                    "0002_add_posts",
                    "0003_add_comments",
                ]

                # 5. Rollback to 0001_initial using --to (should rollback 0003 and 0002)
                run_migrate(to_migration="0001_initial")
                assert test_engine.table_exists("test_users")
                assert not test_engine.table_exists("test_posts")
                assert not test_engine.table_exists("test_comments")
                assert Migrations.get_applied(test_engine) == ["0001_initial"]

                # 6. Test migrate reset (should rollback everything)
                run_migrate(reset=True)
                assert not test_engine.table_exists("test_users")
                assert not test_engine.table_exists("test_posts")
                assert not test_engine.table_exists("test_comments")
                assert len(Migrations.get_applied(test_engine)) == 0

                # 7. Migrate all back to test db schema/explain
                run_migrate()

                # 8. Test run_db_command schema
                mock_args_schema = MagicMock()
                mock_args_schema.db_command = "schema"
                mock_args_schema.database = None
                run_db_command(mock_args_schema)

                # 9. Test run_db_command explain
                mock_args_explain = MagicMock()
                mock_args_explain.db_command = "explain"
                mock_args_explain.query = "SELECT * FROM test_users"
                mock_args_explain.database = None
                run_db_command(mock_args_explain)
    finally:
        os.chdir(orig_cwd)
