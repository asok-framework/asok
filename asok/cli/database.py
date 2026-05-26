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
    rollback: bool = False, status: bool = False, fake: bool = False
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

    Migrations.ensure_table()

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
    applied = Migrations.get_applied()

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
        last_batch_names = Migrations.get_last_batch()
        if not last_batch_names:
            Style.info("Nothing to rollback.")
            return

        Style.heading(f"ROLLBACK (Batch {Migrations.get_last_batch_number()})")
        conn = MigrationConnectionWrapper(Model.get_engine())
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
                    Migrations.remove(name)
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
    batch = Migrations.get_last_batch_number() + 1
    conn = MigrationConnectionWrapper(Model.get_engine())

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
                Migrations.log(name, batch)
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
