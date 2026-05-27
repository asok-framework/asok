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
    rollback: bool = False, status: bool = False, fake: bool = False, database: str | None = None
) -> None:
    """Apply or rollback versioned database migrations."""
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        return
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    if root not in sys.path:
        sys.path.insert(0, root)

    # Load models to ensure DB path is initialized
    Style.info("Loading models...")
    wsgi_path = os.path.join(root, "wsgi.py")
    if os.path.isfile(wsgi_path):
        try:
            spec = _ilu.spec_from_file_location("_wsgi_mig", wsgi_path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            Style.warn(f"Failed to load wsgi.py: {e}")

    model_dir = os.path.join(root, "src/models")
    if os.path.isdir(model_dir):
        for f in sorted(os.listdir(model_dir)):
            # SECURITY: Validate filename to prevent directory traversal
            if ".." in f or "/" in f or "\\" in f:
                continue
            if (f.endswith(".py") or f.endswith(".pyc")) and not f.startswith("__"):
                filepath = os.path.join(model_dir, f)
                # SECURITY: Verify filepath is actually within model_dir
                if not os.path.abspath(filepath).startswith(os.path.abspath(model_dir)):
                    continue
                ext_len = 4 if f.endswith(".pyc") else 3
                mod_name = f"_mig_model_{f[:-ext_len]}"
                try:
                    spec = _ilu.spec_from_file_location(mod_name, filepath)
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                except Exception as e:
                    Style.warn(f"Failed to load model file {f}: {e}")

    # Determine target engine
    if database:
        from ..orm.engines import get_engine
        engine = get_engine(database)
    else:
        engine = Model.get_engine()

    Migrations.ensure_table(engine)

    if MODELS_REGISTRY:
        Style.info(f"Registered models: {', '.join(MODELS_REGISTRY.keys())}")
    else:
        Style.warn("No models found in MODELS_REGISTRY.")

    mig_dir = os.path.join(root, "src/migrations")
    if not os.path.isdir(mig_dir):
        Style.error("No src/migrations/ directory found.")
        return

    # Load all migration files
    # SECURITY: Validate migration filenames to prevent directory traversal
    all_files = os.listdir(mig_dir)
    mig_files = []
    for f in all_files:
        # SECURITY: Skip files with path separators
        if ".." in f or "/" in f or "\\" in f:
            continue
        if f.endswith(".py") and f[:4].isdigit():
            mig_files.append(f)
    mig_files = sorted(mig_files)
    applied = Migrations.get_applied(engine)

    if status:
        Style.heading("MIGRATION STATUS")
        if not mig_files:
            print("  No migrations found.")
            return
        for f in mig_files:
            name = f[:-3]
            is_applied = name in applied
            mark = (
                f"{Style.GREEN}[X]{Style.RESET}"
                if is_applied
                else f"{Style.YELLOW}[ ]{Style.RESET}"
            )
            print(f"  {mark} {name}")
        return

    if rollback:
        last_batch_names = Migrations.get_last_batch(engine)
        if not last_batch_names:
            Style.info("Nothing to rollback.")
            return

        Style.heading(f"ROLLBACK (Batch {Migrations.get_last_batch_number(engine)})")
        conn = MigrationConnectionWrapper(engine)
        try:
            for name in last_batch_names:
                filename = f"{name}.py"
                filepath = os.path.join(mig_dir, filename)
                if not os.path.exists(filepath):
                    Style.error(f"Migration file {filename} missing! Cannot rollback.")
                    continue

                print(f"  Rolling back: {Style.BOLD}{name}{Style.RESET}...")
                spec = _ilu.spec_from_file_location(name, filepath)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)

                if hasattr(mod, "down"):
                    if not fake:
                        mod.down(conn)
                        conn.commit()
                    Migrations.remove(name, engine)
                    Style.success(f"Rolled back {name}")
                else:
                    Style.warn(f"Migration {name} has no down() method.")
        finally:
            conn.close()
        return

    # Forward migration
    pending = [f[:-3] for f in mig_files if f[:-3] not in applied]
    if not pending:
        Style.success("Database is up to date.")
        return

    Style.heading("RUNNING MIGRATIONS")
    batch = Migrations.get_last_batch_number(engine) + 1
    conn = MigrationConnectionWrapper(engine)

    try:
        for name in pending:
            filename = f"{name}.py"
            filepath = os.path.join(mig_dir, filename)
            print(f"  Applying: {Style.BOLD}{name}{Style.RESET}...")

            spec = _ilu.spec_from_file_location(name, filepath)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "up"):
                if not fake:
                    mod.up(conn)
                    conn.commit()
                Migrations.log(name, batch, engine)
                Style.success(f"Applied {name}")
            else:
                Style.warn(f"Migration {name} has no up() method.")
    except Exception as e:
        Style.error(f"Migration failed: {e}")
        traceback.print_exc()
        sys.exit(1)
    finally:
        conn.close()


def run_seed() -> None:
    """Execute the project's seeding script (src/seeds.py) to populate the database."""
    Style.heading("SEEDING DATA")
    sys.path.insert(0, os.getcwd())
    seed_path = os.path.join(os.getcwd(), "src", "seeds.py")
    if not os.path.isfile(seed_path):
        Style.warn("No src/seeds.py found. Create one with a run() function.")
        return

    model_dir = os.path.join(os.getcwd(), "src/models")
    if os.path.isdir(model_dir):
        for filename in sorted(os.listdir(model_dir)):
            # SECURITY: Validate filename to prevent directory traversal
            if ".." in filename or "/" in filename or "\\" in filename:
                continue
            if filename.endswith(".py") and not filename.startswith("__"):
                filepath = os.path.join(model_dir, filename)
                # SECURITY: Verify filepath is actually within model_dir
                if not os.path.abspath(filepath).startswith(os.path.abspath(model_dir)):
                    continue
                spec = _ilu.spec_from_file_location(f"model_{filename}", filepath)
                mod = _ilu.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, Model)
                        and attr is not Model
                    ):
                        # Automatic table creation removed in favor of migrations
                        pass

    spec = _ilu.spec_from_file_location("seeds", seed_path)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if hasattr(mod, "run"):
        mod.run()
        Style.success("Seeding complete.")
    else:
        Style.error("src/seeds.py must define a run() function.")


def run_createsuperuser(email: str | None = None, password: str | None = None) -> None:
    """Interactively create a new administrative user for the project."""
    root = _find_project_root()
    if not root:
        print("Error: Not inside an Asok project (no wsgi.py/c found).")
        sys.exit(1)
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    # Load wsgi entry point to ensure models are registered
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

    Style.heading("CREATE SUPERUSER")
    if not email:
        email = input(f"  {Style.BOLD}Enter your email address:{Style.RESET} ").strip()
    if not password:
        password = getpass.getpass(f"  {Style.BOLD}Enter your password:{Style.RESET} ")
        confirm = getpass.getpass(f"  {Style.BOLD}Confirm your password:{Style.RESET} ")
        if password != confirm:
            Style.error("Passwords don't match.")
            sys.exit(1)
    if not email or not password:
        Style.error("Email and password required.")
        sys.exit(1)

    existing = User.find(email=email)
    if existing:
        existing.password = password
        existing.is_admin = True
        existing.save()
        user = existing
        Style.success(
            f"Updated existing user '{Style.BOLD}{email}{Style.RESET}' as admin."
        )
    else:
        user = User.create(_trust=True, email=email, password=password, is_admin=True)
        Style.success(f"Superuser '{Style.BOLD}{email}{Style.RESET}' created.")

    # Ensure the 'admin' role exists with full permissions and attach it
    Role = MODELS_REGISTRY.get("Role")
    if Role:
        try:
            admin_role = Role.find(name="admin")
            if not admin_role:
                admin_role = Role.create(
                    name="admin", label="Administrator", permissions="*"
                )
                Style.success("Created 'admin' role with full permissions.")
            engine = User.get_engine()
            q_role_user = engine.quote_identifier("role_user")
            q_role_id = engine.quote_identifier("role_id")
            q_user_id = engine.quote_identifier("user_id")

            exists = engine.execute(f"SELECT 1 FROM {q_role_user} WHERE {q_role_id} = ? AND {q_user_id} = ?", (admin_role.id, user.id))
            if not exists:
                engine.execute(f"INSERT INTO {q_role_user} ({q_role_id}, {q_user_id}) VALUES (?, ?)", (admin_role.id, user.id))
        except Exception as e:
            print(f"  ⚠ Could not attach admin role: {e}")


def _load_models(root: str) -> None:
    """Load models dynamically to register them in MODELS_REGISTRY."""
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    if root not in sys.path:
        sys.path.insert(0, root)

    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")
    if os.path.isfile(wsgi_path):
        try:
            spec = _ilu.spec_from_file_location("_wsgi_models", wsgi_path)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            Style.warn(f"Failed to load wsgi.py: {e}")

    model_dir = os.path.join(root, "src/models")
    if os.path.isdir(model_dir):
        for f in sorted(os.listdir(model_dir)):
            if ".." in f or "/" in f or "\\" in f:
                continue
            if (f.endswith(".py") or f.endswith(".pyc")) and not f.startswith("__"):
                filepath = os.path.join(model_dir, f)
                if not os.path.abspath(filepath).startswith(os.path.abspath(model_dir)):
                    continue
                ext_len = 4 if f.endswith(".pyc") else 3
                mod_name = f"_model_load_{f[:-ext_len]}"
                try:
                    spec = _ilu.spec_from_file_location(mod_name, filepath)
                    mod = _ilu.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                except Exception as e:
                    Style.warn(f"Failed to load model file {f}: {e}")


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
    import base64
    import datetime
    import decimal
    import enum
    import json

    from ..orm import FileRef

    # Ensure we are in a valid project root
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        sys.exit(1)

    # Load project models
    _load_models(root)

    # Sync database path with config
    if "DATABASE_URL" in os.environ:
        Model._db_path = (os.environ["DATABASE_URL"] or "").strip() or None

    if not MODELS_REGISTRY:
        Style.warn("No registered models found to dump.")
        return

    # Select target models
    target_models = {}
    if model_name:
        matched = None
        for name, cls in MODELS_REGISTRY.items():
            if name.lower() == model_name.lower():
                matched = (name, cls)
                break
        if not matched:
            Style.error(f"Model '{model_name}' not found in registered models.")
            sys.exit(1)
        target_models[matched[0]] = matched[1]
    else:
        target_models = MODELS_REGISTRY

    fixtures = []
    # Dump records for each target model class
    for name in sorted(target_models.keys()):
        model_cls = target_models[name]
        records = model_cls.all()
        for record in records:
            pk = record.id
            fields_data = {}
            for field_name in model_cls._fields:
                val = getattr(record, field_name)
                # Convert special object types to serializable formats
                if isinstance(val, (datetime.date, datetime.datetime)):
                    val = val.isoformat()
                elif isinstance(val, decimal.Decimal):
                    val = str(val)
                elif isinstance(val, bytes):
                    # Base64-encode binary bytes to keep JSON valid
                    val = "base64:" + base64.b64encode(val).decode("utf-8")
                elif isinstance(val, enum.Enum):
                    val = val.value
                elif isinstance(val, FileRef):
                    val = val.name
                fields_data[field_name] = val

            fixtures.append({
                "model": name,
                "pk": pk,
                "fields": fields_data
            })

    # Output formatted JSON
    json_data = json.dumps(fixtures, indent=2, ensure_ascii=False)
    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(json_data)
            Style.success(f"Successfully dumped {len(fixtures)} records to '{output_file}'.")
        except Exception as e:
            Style.error(f"Failed to write dump to file '{output_file}': {e}")
            sys.exit(1)
    else:
        print(json_data)


def run_loaddata(file_path: str) -> None:
    """Import database records from a JSON fixture file.

    Reads a JSON fixture file and restores the records back into the database.
    To avoid primary key clashes and to preserve original IDs:
    - It checks if a record with the same ID already exists.
    - If it exists, it instantiates the model and performs an UPDATE via ORM .save().
    - If it does not exist, it runs a raw SQL INSERT specifying the 'id' column directly,
      bypassing normal auto-generation.
    The entire operation is wrapped in a single database transaction for safety, speed,
    and atomicity. Binary fields prefixed with 'base64:' are decoded back to raw bytes.

    Args:
        file_path: The file path to the JSON fixture file.
    """
    import base64
    import datetime
    import enum
    import json
    import uuid

    from ..events import events
    from ..orm import FileRef, ModelError
    from ..orm.utils import _RE_EMAIL, _RE_TEL, slugify

    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        sys.exit(1)

    _load_models(root)

    if "DATABASE_URL" in os.environ:
        Model._db_path = (os.environ["DATABASE_URL"] or "").strip() or None

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

    Style.info(f"Loading {len(fixtures)} records...")

    with Model.transaction():
        for index, item in enumerate(fixtures):
            if not isinstance(item, dict) or "model" not in item or "pk" not in item or "fields" not in item:
                Style.error(f"Invalid fixture item at index {index}.")
                sys.exit(1)

            model_name = item["model"]
            pk = item["pk"]
            fields_data = item["fields"]

            matched_cls = None
            for name, cls in MODELS_REGISTRY.items():
                if name.lower() == model_name.lower():
                    matched_cls = cls
                    break
            if not matched_cls:
                Style.error(f"Model '{model_name}' not found in registered models.")
                sys.exit(1)

            processed_fields = {}
            for k, val in fields_data.items():
                if isinstance(val, str) and val.startswith("base64:"):
                    try:
                        val = base64.b64decode(val[7:])
                    except Exception as e:
                        Style.error(f"Failed to decode base64 value for field '{k}' in model '{model_name}': {e}")
                        sys.exit(1)
                processed_fields[k] = val

            engine = matched_cls.get_engine()
            q_table = engine.quote_identifier(matched_cls._table)
            q_id = engine.quote_identifier("id")
            exists_check = engine.execute(f"SELECT 1 FROM {q_table} WHERE {q_id} = ? LIMIT 1", (pk,))
            exists = bool(exists_check)

            if exists:
                instance = matched_cls(_trust=True, id=pk, **processed_fields)
                instance.save()
            else:
                instance = matched_cls(_trust=True, **processed_fields)
                instance.id = pk

                instance.before_save()
                instance.before_create()

                for name in instance._email_fields:
                    val = getattr(instance, name, None)
                    if val in (None, ""):
                        continue
                    if not _RE_EMAIL.match(str(val)):
                        raise ModelError(
                            f"{name.replace('_', ' ').capitalize()} is not a valid email address.",
                            field=name,
                        )

                for name in instance._tel_fields:
                    val = getattr(instance, name, None)
                    if val in (None, ""):
                        continue
                    if not _RE_TEL.match(str(val)):
                        raise ModelError(
                            f"{name.replace('_', ' ').capitalize()} is not a valid phone number.",
                            field=name,
                        )

                for name in instance._password_fields:
                    val = getattr(instance, name)
                    if val and not str(val).startswith("pbkdf2:"):
                        setattr(instance, name, instance._hash_value(str(val)))

                for name in instance._uuid_fields:
                    if not getattr(instance, name):
                        setattr(instance, name, str(uuid.uuid4()))

                for name in instance._slug_fields:
                    field = instance._fields[name]
                    populate = getattr(field, "populate_from", None)
                    always_update = getattr(field, "always_update", False)
                    if populate and (not getattr(instance, name) or always_update):
                        source_val = getattr(instance, populate, None)
                        if source_val:
                            setattr(instance, name, slugify(source_val))

                if instance._timestamp_fields:
                    now = datetime.datetime.now().isoformat()
                    for name in instance._timestamp_fields:
                        field = instance._fields[name]
                        if field.on == "create" and not getattr(instance, name):
                            setattr(instance, name, now)
                        elif field.on == "update":
                            setattr(instance, name, now)

                fields = instance._fields_list
                values = []
                for f in fields:
                    field = instance._fields[f]
                    val = getattr(instance, f)
                    if val is None:
                        values.append(None)
                    elif isinstance(val, FileRef):
                        values.append(val.name)
                    elif hasattr(field, "is_json"):
                        values.append(json.dumps(val))
                    elif hasattr(field, "is_decimal"):
                        values.append(str(val))
                    elif hasattr(field, "is_enum"):
                        if isinstance(val, enum.Enum):
                            values.append(val.value)
                        else:
                            values.append(val)
                    elif hasattr(field, "is_vector"):
                        if val is None:
                            values.append(None)
                        else:
                            if len(val) != field.dimensions:
                                raise ModelError(
                                    f"Vector field '{f}' expects {field.dimensions} dims, got {len(val)}"
                                )
                            values.append(engine.prepare_value(field, val))
                    else:
                        values.append(engine.prepare_value(field, val))

                q_cols = [engine.quote_identifier(f) for f in fields]
                cols_str = ", ".join([q_id] + q_cols)
                placeholders = ", ".join(["?"] * (len(fields) + 1))
                sql = f"INSERT INTO {q_table} ({cols_str}) VALUES ({placeholders})"
                args = [instance.id] + values

                try:
                    engine.execute(sql, args)
                except Exception as e:
                    raise engine.handle_exception(e)

                instance.after_create()
                events.emit(f"model:{instance.__class__.__name__}:created", instance)
                events.emit("model:created", instance)
                instance.after_save()
                events.emit("model:saved", instance)

    Style.success("Successfully loaded fixtures.")

