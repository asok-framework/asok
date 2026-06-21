from __future__ import annotations

import getpass
import importlib.util as _ilu
import os
import sys
import traceback

from ..orm import MODELS_REGISTRY, Migrations, Model
from .server import _find_project_root
from .style import Style


class MigrationConnectionWrapper:
    """Wrapper to make db connections look like sqlite3.Connection with execute/commit/rollback/close."""

    def __init__(self, engine):
        self.engine = engine
        self.conn = engine.get_connection()

    def execute(self, sql, *args, **kwargs):
        # Flatten arguments if passed as a tuple inside a tuple
        params = args[0] if args and isinstance(args[0], (tuple, list)) else args
        return self.engine.execute(sql, params)

    def commit(self):
        if hasattr(self.conn, "commit"):
            self.conn.commit()

    def rollback(self):
        if hasattr(self.conn, "rollback"):
            self.conn.rollback()

    def close(self):
        pass


def run_migrate(
    rollback: bool = False,
    status: bool = False,
    fake: bool = False,
    database: str | None = None,
    to_migration: str | None = None,
    steps: int | None = None,
    reset: bool = False,
) -> None:
    """Apply or rollback versioned database migrations."""
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        return
    _enter_project_root(root)
    _load_app_and_models(root)
    engine = _select_migration_engine(database)
    Migrations.ensure_table(engine)
    _log_registered_models()
    mig_dir = os.path.join(root, "src/migrations")
    if not os.path.isdir(mig_dir):
        Style.error("No src/migrations/ directory found.")
        return
    mig_files = _scan_migration_files(mig_dir)
    applied = Migrations.get_applied(engine)
    if status:
        _print_migration_status(mig_files, applied)
        return
    plan = _plan_migration_action(
        applied, mig_files, rollback, reset, to_migration, steps
    )
    if plan is None:
        return
    _execute_migration_plan(plan, mig_dir, engine, fake)


def _enter_project_root(root: str) -> None:
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_app_and_models(root: str) -> None:
    Style.info("Loading models...")
    _load_wsgi_module(root)
    _load_model_files(root)


def _load_wsgi_module(root: str) -> None:
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        return
    try:
        spec = _ilu.spec_from_file_location("_wsgi_mig", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Style.warn(f"Failed to load wsgi.py: {e}")


def _load_model_files(root: str) -> None:
    model_dir = os.path.join(root, "src/models")
    if not os.path.isdir(model_dir):
        return
    for f in sorted(os.listdir(model_dir)):
        if not _is_loadable_model_file(f):
            continue
        filepath = os.path.join(model_dir, f)
        if not _path_under(filepath, model_dir):
            continue
        _safely_load_model_file(filepath, f)


def _is_loadable_model_file(filename: str) -> bool:
    if _is_traversal_or_dunder(filename):
        return False
    return filename.endswith(".py") or filename.endswith(".pyc")


def _is_traversal_or_dunder(filename: str) -> bool:
    if ".." in filename or "/" in filename or "\\" in filename:
        return True
    return filename.startswith("__")


def _path_under(path: str, base: str) -> bool:
    return os.path.abspath(path).startswith(os.path.abspath(base))


def _safely_load_model_file(filepath: str, filename: str) -> None:
    ext_len = 4 if filename.endswith(".pyc") else 3
    mod_name = f"_mig_model_{filename[:-ext_len]}"
    try:
        spec = _ilu.spec_from_file_location(mod_name, filepath)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Style.warn(f"Failed to load model file {filename}: {e}")


def _select_migration_engine(database: str | None):
    if database:
        from ..orm.engines import get_engine

        return get_engine(database)
    return Model.get_engine()


def _log_registered_models() -> None:
    if MODELS_REGISTRY:
        Style.info(f"Registered models: {', '.join(MODELS_REGISTRY.keys())}")
    else:
        Style.warn("No models found in MODELS_REGISTRY.")


def _scan_migration_files(mig_dir: str) -> list[str]:
    return sorted(f for f in os.listdir(mig_dir) if _is_migration_filename(f))


def _is_migration_filename(filename: str) -> bool:
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return filename.endswith(".py") and filename[:4].isdigit()


def _print_migration_status(mig_files: list[str], applied: list[str]) -> None:
    Style.heading("MIGRATION STATUS")
    if not mig_files:
        print("  No migrations found.")
        return
    for f in mig_files:
        name = f[:-3]
        mark = _status_mark(name in applied)
        print(f"  {mark} {name}")


def _status_mark(is_applied: bool) -> str:
    if is_applied:
        return f"{Style.GREEN}[X]{Style.RESET}"
    return f"{Style.YELLOW}[ ]{Style.RESET}"


def _plan_migration_action(
    applied: list[str], mig_files: list[str], rollback: bool, reset: bool,
    to_migration: str | None, steps: int | None,
):
    if reset:
        return _plan_reset(applied)
    if to_migration:
        return _plan_to_migration(applied, mig_files, to_migration)
    if rollback:
        return _plan_rollback(applied, steps)
    return _plan_default_apply(applied, mig_files)


def _plan_reset(applied: list[str]):
    if not applied:
        Style.info("No migrations to rollback.")
        return None
    Style.heading("ROLLBACK ALL MIGRATIONS")
    return ("rollback", list(reversed(applied)))


def _plan_to_migration(applied: list[str], mig_files: list[str], to_migration: str):
    target_mig = _resolve_target_migration(mig_files, to_migration)
    if target_mig is None:
        Style.error(f"Migration '{to_migration}' not found.")
        return None
    if target_mig in applied:
        return _plan_rollback_to(applied, target_mig)
    return _plan_apply_up_to(applied, mig_files, target_mig)


def _resolve_target_migration(mig_files: list[str], to_migration: str):
    all_names = [f[:-3] for f in mig_files]
    for name in all_names:
        if _matches_target_name(name, to_migration):
            return name
    return None


def _matches_target_name(name: str, target: str) -> bool:
    if name == target or name.startswith(target + "_"):
        return True
    return name.split("_", 1)[0] == target


def _plan_rollback_to(applied: list[str], target_mig: str):
    idx = applied.index(target_mig)
    to_rollback = applied[idx + 1:]
    if not to_rollback:
        Style.success(f"Database is already at migration '{target_mig}'.")
        return None
    Style.heading(f"ROLLBACK TO MIGRATION '{target_mig}'")
    return ("rollback", list(reversed(to_rollback)))


def _plan_apply_up_to(applied: list[str], mig_files: list[str], target_mig: str):
    all_names = [f[:-3] for f in mig_files]
    idx = all_names.index(target_mig)
    to_apply = [m for m in all_names[: idx + 1] if m not in applied]
    if not to_apply:
        Style.success(f"Database is already at or past migration '{target_mig}'.")
        return None
    Style.heading(f"MIGRATING UP TO '{target_mig}'")
    return ("migrate", to_apply)


def _plan_rollback(applied: list[str], steps: int | None):
    if steps is None:
        return _plan_last_batch_rollback(applied)
    if steps <= 0:
        Style.error("Steps must be a positive integer.")
        return None
    to_rollback = applied[-steps:]
    if not to_rollback:
        Style.info("Nothing to rollback.")
        return None
    Style.heading(f"ROLLBACK (Last {len(to_rollback)} migrations)")
    return ("rollback", list(reversed(to_rollback)))


def _plan_last_batch_rollback(applied: list[str]):
    # NB: the live engine is loaded later; we rely on Model.get_engine().
    engine = Model.get_engine()
    last_batch_names = Migrations.get_last_batch(engine)
    if not last_batch_names:
        Style.info("Nothing to rollback.")
        return None
    Style.heading(f"ROLLBACK (Batch {Migrations.get_last_batch_number(engine)})")
    return ("rollback", last_batch_names)


def _plan_default_apply(applied: list[str], mig_files: list[str]):
    pending = [f[:-3] for f in mig_files if f[:-3] not in applied]
    if not pending:
        Style.success("Database is up to date.")
        return None
    Style.heading("RUNNING MIGRATIONS")
    return ("migrate", pending)


def _execute_migration_plan(plan, mig_dir: str, engine, fake: bool) -> None:
    action, names = plan
    conn = MigrationConnectionWrapper(engine)
    try:
        if action == "rollback":
            _run_rollbacks(conn, engine, mig_dir, names, fake)
        else:
            batch = Migrations.get_last_batch_number(engine) + 1
            _run_applies(conn, engine, mig_dir, names, batch, fake)
    except Exception as e:
        Style.error(f"Migration operation failed: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


def _get_schema_editor(conn, engine):
    from asok.orm.engines.mysql import MySQLEngine
    from asok.orm.engines.postgres import PostgresEngine
    from asok.orm.engines.sqlite import SQLiteEngine
    from asok.orm.migrations.schema import (
        MySQLSchemaEditor,
        PostgresSchemaEditor,
        SQLiteSchemaEditor,
    )

    if isinstance(engine, SQLiteEngine):
        return SQLiteSchemaEditor(conn, engine)
    elif isinstance(engine, PostgresEngine):
        return PostgresSchemaEditor(conn, engine)
    elif isinstance(engine, MySQLEngine):
        return MySQLSchemaEditor(conn, engine)
    else:
        from asok.orm.migrations.schema import BaseSchemaEditor
        return BaseSchemaEditor(conn, engine)


def _run_rollbacks(conn, engine, mig_dir: str, names: list[str], fake: bool) -> None:
    for name in names:
        _rollback_one(conn, engine, mig_dir, name, fake)


def _rollback_one(conn, engine, mig_dir: str, name: str, fake: bool) -> None:
    filename = f"{name}.py"
    filepath = os.path.join(mig_dir, filename)
    if not os.path.exists(filepath):
        Style.error(f"Migration file {filename} missing! Cannot rollback.")
        return
    print(f"  Rolling back: {Style.BOLD}{name}{Style.RESET}...")
    mod = _load_migration_module(name, filepath)

    if hasattr(mod, "Migration"):
        _rollback_declarative_migration(conn, engine, mod.Migration, fake)
    else:
        _rollback_functional_migration(conn, mod, name, fake)

    Migrations.remove(name, engine)
    Style.success(f"Rolled back {name}")


def _rollback_declarative_migration(conn, engine, migration_cls, fake: bool) -> None:
    if fake:
        return
    migration = migration_cls()
    editor = _get_schema_editor(conn, engine)
    txn = getattr(engine, "transaction", None)
    context = txn() if txn else None
    ops = list(reversed(getattr(migration, "operations", [])))
    _execute_ops_backwards_with_context(context, ops, editor)
    conn.commit()


def _execute_ops_backwards_with_context(context, ops, editor) -> None:
    if context:
        with context:
            for op in ops:
                op.database_backwards(editor)
    else:
        for op in ops:
            op.database_backwards(editor)


def _rollback_functional_migration(conn, mod, name: str, fake: bool) -> None:
    if not hasattr(mod, "down"):
        Style.warn(f"Migration {name} has no down() method or Migration class.")
        return
    if not fake:
        mod.down(conn)
        conn.commit()


def _run_applies(conn, engine, mig_dir: str, names: list[str], batch: int, fake: bool) -> None:
    for name in names:
        _apply_one(conn, engine, mig_dir, name, batch, fake)


def _apply_one(conn, engine, mig_dir: str, name: str, batch: int, fake: bool) -> None:
    filename = f"{name}.py"
    filepath = os.path.join(mig_dir, filename)
    print(f"  Applying: {Style.BOLD}{name}{Style.RESET}...")
    mod = _load_migration_module(name, filepath)

    if hasattr(mod, "Migration"):
        _apply_declarative_migration(conn, engine, mod.Migration, fake)
    else:
        _apply_functional_migration(conn, mod, name, fake)

    Migrations.log(name, batch, engine)
    Style.success(f"Applied {name}")


def _apply_declarative_migration(conn, engine, migration_cls, fake: bool) -> None:
    if fake:
        return
    migration = migration_cls()
    editor = _get_schema_editor(conn, engine)
    txn = getattr(engine, "transaction", None)
    context = txn() if txn else None
    _execute_ops_forwards_with_context(context, getattr(migration, "operations", []), editor)
    conn.commit()


def _execute_ops_forwards_with_context(context, ops, editor) -> None:
    if context:
        with context:
            for op in ops:
                op.database_forwards(editor)
    else:
        for op in ops:
            op.database_forwards(editor)


def _apply_functional_migration(conn, mod, name: str, fake: bool) -> None:
    if not hasattr(mod, "up"):
        Style.warn(f"Migration {name} has no up() method or Migration class.")
        return
    if not fake:
        mod.up(conn)
        conn.commit()


def _load_migration_module(name: str, filepath: str):
    spec = _ilu.spec_from_file_location(name, filepath)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_seed() -> None:
    """Execute the project's seeding script (src/seeds.py) to populate the database."""
    Style.heading("SEEDING DATA")
    sys.path.insert(0, os.getcwd())
    seed_path = os.path.join(os.getcwd(), "src", "seeds.py")
    if not os.path.isfile(seed_path):
        Style.warn("No src/seeds.py found. Create one with a run() function.")
        return
    _import_seed_model_files(os.path.join(os.getcwd(), "src/models"))
    _run_seed_module(seed_path)


def _import_seed_model_files(model_dir: str) -> None:
    if not os.path.isdir(model_dir):
        return
    for filename in sorted(os.listdir(model_dir)):
        if _is_seedable_model_filename(filename):
            _import_seed_model(model_dir, filename)


def _is_seedable_model_filename(filename: str) -> bool:
    if ".." in filename or "/" in filename or "\\" in filename:
        return False
    return filename.endswith(".py") and not filename.startswith("__")


def _import_seed_model(model_dir: str, filename: str) -> None:
    filepath = os.path.join(model_dir, filename)
    if not _path_under(filepath, model_dir):
        return
    spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)


def _run_seed_module(seed_path: str) -> None:
    spec = _ilu.spec_from_file_location("seeds", seed_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "run"):
        Style.error("src/seeds.py must define a run() function.")
        return
    mod.run()
    Style.success("Seeding complete.")


def run_createsuperuser(email: str | None = None, password: str | None = None) -> None:
    """Interactively create a new administrative user for the project."""
    root = _find_project_root()
    if not root:
        print("Error: Not inside an Asok project (no wsgi.py/c found).")
        sys.exit(1)
    _enter_project_root(root)
    User = _load_user_model(root)
    Style.heading("CREATE SUPERUSER")
    email, password = _prompt_superuser_credentials(email, password)
    user = _upsert_admin_user(User, email, password)
    _ensure_admin_role(user)


def _load_user_model(root: str):
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")
    spec = _ilu.spec_from_file_location("_wsgi", wsgi_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    User = MODELS_REGISTRY.get(getattr(mod, "app").config.get("AUTH_MODEL", "User"))
    if not User:
        Style.error("User model not found.")
        sys.exit(1)
    return User


def _prompt_superuser_credentials(email, password) -> tuple[str, str]:
    if not email:
        email = input(f"  {Style.BOLD}Enter your email address:{Style.RESET} ").strip()
    if not password:
        password = _prompt_password_with_confirmation()
    if not email or not password:
        Style.error("Email and password required.")
        sys.exit(1)
    return email, password


def _prompt_password_with_confirmation() -> str:
    password = getpass.getpass(f"  {Style.BOLD}Enter your password:{Style.RESET} ")
    confirm = getpass.getpass(f"  {Style.BOLD}Confirm your password:{Style.RESET} ")
    if password != confirm:
        Style.error("Passwords don't match.")
        sys.exit(1)
    return password


def _upsert_admin_user(User, email: str, password: str):
    existing = User.find(email=email)
    if existing:
        existing.password = password
        existing.is_admin = True
        existing.save()
        Style.success(
            f"Updated existing user '{Style.BOLD}{email}{Style.RESET}' as admin."
        )
        return existing
    user = User.create(_trust=True, email=email, password=password, is_admin=True)
    Style.success(f"Superuser '{Style.BOLD}{email}{Style.RESET}' created.")
    return user


def _ensure_admin_role(user) -> None:
    Role = MODELS_REGISTRY.get("Role")
    if not Role:
        return
    try:
        admin_role = _get_or_create_admin_role(Role)
        _attach_user_to_role(user, admin_role)
    except Exception as e:
        print(f"  ⚠ Could not attach admin role: {e}")


def _get_or_create_admin_role(Role):
    admin_role = Role.find(name="admin")
    if admin_role:
        return admin_role
    admin_role = Role.create(name="admin", label="Administrator", permissions="*")
    Style.success("Created 'admin' role with full permissions.")
    return admin_role


def _attach_user_to_role(user, admin_role) -> None:
    engine = user.__class__.get_engine()
    q_role_user = engine.quote_identifier("role_user")
    q_role_id = engine.quote_identifier("role_id")
    q_user_id = engine.quote_identifier("user_id")
    exists = engine.execute(
        f"SELECT 1 FROM {q_role_user} WHERE {q_role_id} = ? AND {q_user_id} = ?",
        (admin_role.id, user.id),
    )
    if exists:
        return
    engine.execute(
        f"INSERT INTO {q_role_user} ({q_role_id}, {q_user_id}) VALUES (?, ?)",
        (admin_role.id, user.id),
    )


def _load_models(root: str) -> None:
    """Load models dynamically to register them in MODELS_REGISTRY."""
    _enter_project_root(root)
    _load_wsgi_for_models(root)
    _load_model_dir(root)


def _load_wsgi_for_models(root: str) -> None:
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")
    if not os.path.isfile(wsgi_path):
        return
    try:
        spec = _ilu.spec_from_file_location("_wsgi_models", wsgi_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Style.warn(f"Failed to load wsgi.py: {e}")


def _load_model_dir(root: str) -> None:
    model_dir = os.path.join(root, "src/models")
    if not os.path.isdir(model_dir):
        return
    for f in sorted(os.listdir(model_dir)):
        if not _is_loadable_model_file(f):
            continue
        filepath = os.path.join(model_dir, f)
        if _path_under(filepath, model_dir):
            _import_model_file(filepath, f)


def _import_model_file(filepath: str, filename: str) -> None:
    ext_len = 4 if filename.endswith(".pyc") else 3
    mod_name = f"_model_load_{filename[:-ext_len]}"
    try:
        spec = _ilu.spec_from_file_location(mod_name, filepath)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Style.warn(f"Failed to load model file {filename}: {e}")


def run_dumpdata(model_name: str | None = None, output_file: str | None = None) -> None:
    """Export database records to a JSON fixture file.

    This command serializes database table records into a JSON fixture format.
    If no model_name is specified, all registered models will be serialized.
    Special database field types (e.g. datetimes, decimals, enums, files) are converted
    to serializable formats. Binary BLOB fields (bytes) are base64-encoded with a
    special 'base64:' prefix to prevent encoding issues.

    Args:
        model_name: The name of the specific model to dump (case-insensitive).
        output_file: The target file path to write the JSON data to. If not provided,
                     the JSON string will be printed to stdout.
    """
    import json

    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        sys.exit(1)
    _load_models(root)
    _sync_db_path_from_env()
    if not MODELS_REGISTRY:
        Style.warn("No registered models found to dump.")
        return
    target_models = _select_dump_targets(model_name)
    if target_models is None:
        return
    fixtures = _collect_fixtures(target_models)
    _emit_dump_output(fixtures, output_file, json)


def _select_dump_targets(model_name: str | None):
    if not model_name:
        return MODELS_REGISTRY
    target = model_name.lower()
    for name, cls in MODELS_REGISTRY.items():
        if name.lower() == target:
            return {name: cls}
    Style.error(f"Model '{model_name}' not found in registered models.")
    sys.exit(1)


def _collect_fixtures(target_models: dict) -> list:
    fixtures: list = []
    for name in sorted(target_models.keys()):
        model_cls = target_models[name]
        for record in model_cls.all():
            fixtures.append(
                {"model": name, "pk": record.id, "fields": _serialize_record_fields(model_cls, record)}
            )
    return fixtures


def _serialize_record_fields(model_cls, record) -> dict:
    return {
        field_name: _serialize_dump_value(getattr(record, field_name))
        for field_name in model_cls._fields
    }


def _serialize_dump_value(val):
    converter = _dump_value_converter(val)
    return converter(val) if converter else val


def _dump_value_converter(val):
    import datetime

    if isinstance(val, (datetime.date, datetime.datetime)):
        return lambda v: v.isoformat()
    if isinstance(val, bytes):
        return _encode_bytes_base64
    return _other_dump_converter(val)


def _other_dump_converter(val):
    import decimal
    import enum

    from ..orm import FileRef

    if isinstance(val, decimal.Decimal):
        return str
    if isinstance(val, enum.Enum):
        return lambda v: v.value
    if isinstance(val, FileRef):
        return lambda v: v.name
    return None


def _encode_bytes_base64(val: bytes) -> str:
    import base64

    return "base64:" + base64.b64encode(val).decode("utf-8")


def _emit_dump_output(fixtures: list, output_file: str | None, json_mod) -> None:
    json_data = json_mod.dumps(fixtures, indent=2, ensure_ascii=False)
    if not output_file:
        print(json_data)
        return
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(json_data)
        Style.success(
            f"Successfully dumped {len(fixtures)} records to '{output_file}'."
        )
    except Exception as e:
        Style.error(f"Failed to write dump to file '{output_file}': {e}")
        sys.exit(1)


def run_loaddata(file_path: str) -> None:
    """Import database records from a JSON fixture file.

    Reads a JSON fixture file and restores the records back into the database.
    To avoid primary key clashes and to preserve original IDs:
    - It checks if a record with the same ID already exists.
    - If it exists, it instantiates the model and performs an UPDATE via ORM .save().
    - If it does not exist, it runs a raw SQL INSERT specifying the 'id' column directly,
      bypassing normal auto-generation.
    The entire operation is wrapped in a single database transaction for safety,
    speed, and atomicity. Binary fields prefixed with 'base64:' are decoded back
    to raw bytes.

    Args:
        file_path: The file path to the JSON fixture file.
    """
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        sys.exit(1)
    _load_models(root)
    _sync_db_path_from_env()
    fixtures = _read_fixtures(file_path)
    Style.info(f"Loading {len(fixtures)} records...")
    with Model.transaction():
        for index, item in enumerate(fixtures):
            _load_one_fixture(index, item)
    Style.success("Successfully loaded fixtures.")


def _sync_db_path_from_env() -> None:
    if "DATABASE_URL" in os.environ:
        Model._db_path = (os.environ["DATABASE_URL"] or "").strip() or None


def _read_fixtures(file_path: str):
    import json

    if not os.path.exists(file_path):
        Style.error(f"Fixture file '{file_path}' does not exist.")
        sys.exit(1)
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            fixtures = json.load(f)
    except Exception as e:
        Style.error(f"Failed to parse JSON fixture file: {e}")
        sys.exit(1)
    if not isinstance(fixtures, list):
        Style.error("Invalid fixture format: root element must be a JSON list.")
        sys.exit(1)
    return fixtures


def _load_one_fixture(index: int, item) -> None:
    if not _is_valid_fixture_item(item):
        Style.error(f"Invalid fixture item at index {index}.")
        sys.exit(1)
    model_name, pk, fields_data = item["model"], item["pk"], item["fields"]
    matched_cls = _resolve_fixture_model(model_name)
    if matched_cls is None:
        Style.error(f"Model '{model_name}' not found in registered models.")
        sys.exit(1)
    processed_fields = _decode_fixture_fields(fields_data, model_name)
    _upsert_fixture_instance(matched_cls, pk, processed_fields)


def _is_valid_fixture_item(item) -> bool:
    if not isinstance(item, dict):
        return False
    return "model" in item and "pk" in item and "fields" in item


def _resolve_fixture_model(model_name: str):
    target = model_name.lower()
    for name, cls in MODELS_REGISTRY.items():
        if name.lower() == target:
            return cls
    return None


def _decode_fixture_fields(fields_data: dict, model_name: str) -> dict:
    import base64

    processed: dict = {}
    for k, val in fields_data.items():
        if isinstance(val, str) and val.startswith("base64:"):
            val = _decode_base64_value(val, k, model_name, base64)
        processed[k] = val
    return processed


def _decode_base64_value(val: str, field_name: str, model_name: str, base64_mod):
    try:
        return base64_mod.b64decode(val[7:])
    except Exception as e:
        Style.error(
            f"Failed to decode base64 value for field '{field_name}' in model '{model_name}': {e}"
        )
        sys.exit(1)


def _upsert_fixture_instance(matched_cls, pk, processed_fields: dict) -> None:
    engine = matched_cls.get_engine()
    q_table = engine.quote_identifier(matched_cls._table)
    q_id = engine.quote_identifier("id")
    if _row_with_pk_exists(engine, q_table, q_id, pk):
        instance = matched_cls(_trust=True, id=pk, **processed_fields)
        instance.save()
        return
    _insert_new_fixture(matched_cls, pk, processed_fields, engine, q_table, q_id)


def _row_with_pk_exists(engine, q_table: str, q_id: str, pk) -> bool:
    rows = engine.execute(
        f"SELECT 1 FROM {q_table} WHERE {q_id} = ? LIMIT 1", (pk,)
    )
    return bool(rows)


def _insert_new_fixture(matched_cls, pk, processed_fields: dict, engine, q_table: str, q_id: str) -> None:
    instance = matched_cls(_trust=True, **processed_fields)
    instance.id = pk
    instance._fire_pre_save_hooks()
    instance._validate_email_fields()
    instance._validate_tel_fields()
    instance._hash_password_fields()
    instance._assign_uuid_fields()
    instance._populate_slug_fields()
    instance._apply_timestamp_fields()
    values = instance._serialize_fields(engine)
    _execute_fixture_insert(engine, q_table, q_id, instance, values)
    _fire_fixture_post_hooks(instance)


def _execute_fixture_insert(engine, q_table: str, q_id: str, instance, values) -> None:
    fields = instance._fields_list
    q_cols = [engine.quote_identifier(f) for f in fields]
    cols_str = ", ".join([q_id] + q_cols)
    placeholders = ", ".join(["?"] * (len(fields) + 1))
    sql = f"INSERT INTO {q_table} ({cols_str}) VALUES ({placeholders})"
    try:
        engine.execute(sql, [instance.id] + values)
    except Exception as e:
        raise engine.handle_exception(e)


def _fire_fixture_post_hooks(instance) -> None:
    from ..events import events

    instance.after_create()
    events.emit(f"model:{instance.__class__.__name__}:created", instance)
    events.emit("model:created", instance)
    instance.after_save()
    events.emit("model:saved", instance)


def run_db_command(args) -> None:
    """Execute db subcommands: schema, explain."""
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        sys.exit(1)
    _enter_project_root(root)
    _load_models(root)
    engine = _select_migration_engine(getattr(args, "database", None))
    if args.db_command == "schema":
        _show_schema(engine)
    elif args.db_command == "explain":
        _show_explain(engine, args.query)


def _show_schema(engine) -> None:
    handler = _SCHEMA_HANDLERS.get(engine.__class__.__name__)
    if handler is None:
        Style.error(
            f"Schema introspection is not supported on engine {engine.__class__.__name__}."
        )
        return
    handler(engine)


def _show_explain(engine, query: str) -> None:
    Style.heading("EXPLAIN QUERY PLAN")
    handler = _EXPLAIN_HANDLERS.get(engine.__class__.__name__)
    if handler is None:
        Style.error(
            f"Explain query is not supported on engine {engine.__class__.__name__}."
        )
        return
    try:
        handler(engine, query)
    except Exception as e:
        Style.error(f"Failed to explain query: {e}")


def _sqlite_schema(engine) -> None:
    Style.heading("DATABASE SCHEMA (SQLite)")
    tables = engine.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    table_names = [t["name"] for t in tables]
    if not table_names:
        print("  No tables found.")
        return
    for table in table_names:
        _print_sqlite_table(engine, table)


def _print_sqlite_table(engine, table: str) -> None:
    print(f"\nTable: {Style.BOLD}{table}{Style.RESET}")
    columns = engine.execute(f"PRAGMA table_info({engine.quote_identifier(table)})")
    for col in columns:
        _print_sqlite_column(col)
    fks = engine.execute(f"PRAGMA foreign_key_list({engine.quote_identifier(table)})")
    if fks:
        print("  Foreign Keys:")
        for fk in fks:
            print(f"    - {fk['from']} -> {fk['table']}({fk['to']})")


def _print_sqlite_column(col) -> None:
    pk_str = " (PK)" if col["pk"] else ""
    notnull_str = " NOT NULL" if col["notnull"] else ""
    default_str = f" DEFAULT {col['dflt_value']}" if col["dflt_value"] is not None else ""
    print(f"  - {col['name']}: {col['type']}{pk_str}{notnull_str}{default_str}")


def _postgres_schema(engine) -> None:
    Style.heading("DATABASE SCHEMA (PostgreSQL)")
    tables = engine.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    )
    table_names = [t["table_name"] for t in tables]
    if not table_names:
        print("  No tables found.")
        return
    for table in table_names:
        _print_postgres_table(engine, table)


def _print_postgres_table(engine, table: str) -> None:
    print(f"\nTable: {Style.BOLD}{table}{Style.RESET}")
    columns = engine.execute(
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name = ?",
        (table,),
    )
    pks = _postgres_table_pks(engine, table)
    for col in columns:
        _print_postgres_column(col, pks)
    _print_postgres_fks(engine, table)


def _postgres_table_pks(engine, table: str) -> list[str]:
    pk_rows = engine.execute(
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.constraint_type = 'PRIMARY KEY' "
        "AND tc.table_schema = 'public' AND tc.table_name = ?",
        (table,),
    )
    return [row["column_name"] for row in pk_rows] if pk_rows else []


def _print_postgres_column(col, pks: list[str]) -> None:
    name = col["column_name"]
    dtype = col["data_type"]
    pk_str = " (PK)" if name in pks else ""
    notnull_str = " NOT NULL" if col["is_nullable"] == "NO" else ""
    default_str = (
        f" DEFAULT {col['column_default']}" if col["column_default"] is not None else ""
    )
    print(f"  - {name}: {dtype}{pk_str}{notnull_str}{default_str}")


def _print_postgres_fks(engine, table: str) -> None:
    fk_rows = engine.execute(
        "SELECT kcu.column_name as local_col, ccu.table_name as ref_table, "
        "ccu.column_name as ref_col "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON ccu.constraint_name = tc.constraint_name "
        "WHERE tc.constraint_type = 'FOREIGN KEY' "
        "AND tc.table_schema = 'public' AND tc.table_name = ?",
        (table,),
    )
    if fk_rows:
        print("  Foreign Keys:")
        for fk in fk_rows:
            print(f"    - {fk['local_col']} -> {fk['ref_table']}({fk['ref_col']})")


def _mysql_schema(engine) -> None:
    Style.heading("DATABASE SCHEMA (MySQL)")
    tables = engine.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
    )
    table_names = [t["table_name"] for t in tables]
    if not table_names:
        print("  No tables found.")
        return
    for table in table_names:
        _print_mysql_table(engine, table)


def _print_mysql_table(engine, table: str) -> None:
    print(f"\nTable: {Style.BOLD}{table}{Style.RESET}")
    columns = engine.execute(f"DESCRIBE {engine.quote_identifier(table)}")
    for col in columns:
        _print_mysql_column(col)
    _print_mysql_fks(engine, table)


def _print_mysql_column(col) -> None:
    pk_str = " (PK)" if col["Key"] == "PRI" else ""
    notnull_str = " NOT NULL" if col["Null"] == "NO" else ""
    default_str = f" DEFAULT {col['Default']}" if col["Default"] is not None else ""
    print(f"  - {col['Field']}: {col['Type']}{pk_str}{notnull_str}{default_str}")


def _print_mysql_fks(engine, table: str) -> None:
    fk_rows = engine.execute(
        "SELECT column_name as local_col, referenced_table_name as ref_table, "
        "referenced_column_name as ref_col "
        "FROM information_schema.key_column_usage "
        "WHERE table_schema = DATABASE() AND table_name = ? "
        "AND referenced_table_name IS NOT NULL",
        (table,),
    )
    if fk_rows:
        print("  Foreign Keys:")
        for fk in fk_rows:
            print(f"    - {fk['local_col']} -> {fk['ref_table']}({fk['ref_col']})")


def _explain_sqlite(engine, query: str) -> None:
    res = engine.execute(f"EXPLAIN QUERY PLAN {query}")
    for row in res:
        print(f"  {row.get('detail', '')}")


def _explain_postgres(engine, query: str) -> None:
    res = engine.execute(f"EXPLAIN {query}")
    for row in res:
        print(f"  {list(row.values())[0]}")


def _explain_mysql(engine, query: str) -> None:
    res = engine.execute(f"EXPLAIN {query}")
    for row in res:
        items = [f"{k}: {v}" for k, v in row.items() if v is not None]
        print("  " + " | ".join(items))


_SCHEMA_HANDLERS = {
    "SQLiteEngine": _sqlite_schema,
    "PostgresEngine": _postgres_schema,
    "MySQLEngine": _mysql_schema,
}

_EXPLAIN_HANDLERS = {
    "SQLiteEngine": _explain_sqlite,
    "PostgresEngine": _explain_postgres,
    "MySQLEngine": _explain_mysql,
}
