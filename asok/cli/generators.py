from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import time
from typing import Any

from ..orm import MODELS_REGISTRY, slugify
from .scaffold import _ensure_init_py
from .server import _find_project_root
from .style import Style


def _is_valid_scaffold_name(name, label: str) -> bool:
    """SECURITY: refuse names that could trigger path traversal or shell tricks."""
    if not _check_name_basics(name, label):
        return False
    return _check_name_chars(name, label) and _check_name_no_separators(name, label)


def _check_name_chars(name: str, label: str) -> bool:
    if name.replace("_", "").replace("-", "").isalnum():
        return True
    Style.error(
        f"{label.capitalize()} name must contain only letters, numbers, hyphens, "
        "and underscores"
    )
    return False


def _check_name_no_separators(name: str, label: str) -> bool:
    if ".." not in name and "/" not in name and "\\" not in name:
        return True
    Style.error(f"{label.capitalize()} name cannot contain path separators")
    return False


def _check_name_basics(name, label: str) -> bool:
    if not name or not isinstance(name, str):
        Style.error(f"Invalid {label} name")
        return False
    if len(name) > 100:
        Style.error(f"{label.capitalize()} name too long (max 100 characters)")
        return False
    return True


def make_model(name: str) -> None:
    """Generate a model file in src/models/."""
    if not _is_valid_scaffold_name(name, "model"):
        return
    os.makedirs("src/models", exist_ok=True)
    _ensure_init_py("src/models")
    filename = f"src/models/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return
    content = f"""\
from asok import Model, Field

class {name.capitalize()}(Model):
    name = Field.String()
    created_at = Field.CreatedAt()
    updated_at = Field.UpdatedAt()
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"File created: {Style.BOLD}{filename}{Style.RESET}")


def make_middleware(name: str) -> None:
    """Generate a middleware file in src/middlewares/."""
    if not _is_valid_scaffold_name(name, "middleware"):
        return
    os.makedirs("src/middlewares", exist_ok=True)
    _ensure_init_py("src/middlewares")
    filename = f"src/middlewares/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return
    content = """\
def handle(request, next):
    # Pre-processing
    response = next(request)
    # Post-processing
    return response
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"File created: {Style.BOLD}{filename}{Style.RESET}")


def make_migration(name: str) -> None:
    """Detect model changes and generate a new migration file.

    SECURITY: Validates name to prevent path traversal attacks.
    """
    if not _is_valid_migration_name(name):
        return
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        return
    _enter_project_root_for_migrations(root)
    Style.info("Analyzing project models...")
    wsgi_mod = _load_wsgi_for_migrations(root)
    _scan_models_dir_for_migrations(root)
    _inject_admin_user_methods(wsgi_mod)
    mig_dir = os.path.join(root, "src/migrations")
    os.makedirs(mig_dir, exist_ok=True)
    _ensure_init_py(mig_dir)
    _announce_registered_models()

    _do_make_migration(name, mig_dir)


def _do_make_migration(name: str, mig_dir: str) -> None:
    mig_files = _existing_migration_files(mig_dir)
    historical_state = _reconstruct_historical_state(mig_dir, mig_files)

    from asok.orm import MODELS_REGISTRY
    from asok.orm.migrations.state import ProjectState
    current_state = ProjectState.from_codebase(MODELS_REGISTRY)

    from asok.orm.migrations.autodetector import MigrationAutodetector
    autodetector = MigrationAutodetector(historical_state, current_state)
    operations = autodetector.changes()

    if not operations:
        Style.info("No changes detected in models.")
        return

    _write_migration_file(name, mig_dir, mig_files, operations)


def _write_migration_file(name: str, mig_dir: str, mig_files: list[str], operations: list[Any]) -> None:
    dependencies = []
    if mig_files:
        dependencies.append(mig_files[-1][:-3]) # Strip .py

    filepath = _resolve_migration_filepath(name, mig_dir)
    if filepath is None:
        return

    content = _build_declarative_migration_file_content(name, dependencies, operations)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    Style.success(f"Created migration: {Style.BOLD}{os.path.basename(filepath)}{Style.RESET}")


def _resolve_migration_filepath(name: str, mig_dir: str):
    slug = _safe_migration_slug(name)
    if slug is None:
        return None
    existing = _existing_migration_files(mig_dir)
    next_num = max([int(f[:4]) for f in existing] + [0]) + 1
    filepath = os.path.join(mig_dir, f"{next_num:04d}_{slug}.py")
    if not os.path.abspath(filepath).startswith(os.path.abspath(mig_dir)):
        Style.error("Security error: invalid migration path")
        return None
    return filepath


def _safe_migration_slug(name: str):
    slug = slugify(name)
    if ".." in slug or "/" in slug or "\\" in slug:
        Style.error("Invalid migration name after slugification")
        return None
    return slug


def _existing_migration_files(mig_dir: str) -> list[str]:
    return [f for f in os.listdir(mig_dir) if _is_versioned_migration_file(f)]


def _is_versioned_migration_file(f: str) -> bool:
    if ".." in f or "/" in f or "\\" in f:
        return False
    return f.endswith(".py") and f[:4].isdigit()



def _classify_migrations(mig_dir: str, mig_files: list[str]) -> tuple[list, list]:
    import importlib.util as _ilu
    class_migrations = []
    legacy_files = []

    for f in mig_files:
        filepath = os.path.join(mig_dir, f)
        name = f[:-3]
        try:
            spec = _ilu.spec_from_file_location(name, filepath)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:
            continue

        if hasattr(mod, "Migration"):
            class_migrations.append((name, mod.Migration))
        else:
            legacy_files.append(mod)

    return class_migrations, legacy_files


def _apply_class_migrations_to_state(state: Any, class_migrations: list) -> None:
    for name, MigrationClass in class_migrations:
        migration = MigrationClass()
        for op in getattr(migration, "operations", []):
            op.state_forwards(state)


def _run_legacy_in_memory(legacy_files: list) -> Any:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    class MemoryConnWrapper:
        def __init__(self, db_conn):
            self.db_conn = db_conn
        def execute(self, sql, *args):
            return self.db_conn.execute(sql, *args)

    mem_conn = MemoryConnWrapper(conn)
    for mod in legacy_files:
        if hasattr(mod, "up"):
            try:
                mod.up(mem_conn)
            except Exception:
                pass
    return conn


def _inspect_legacy_sqlite_tables(conn: Any) -> dict:
    from asok.orm.migrations.state import VirtualModelState
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_asok_%'")
    tables = [row[0] for row in cursor.fetchall()]

    models = {}
    for table in tables:
        fields = _inspect_legacy_sqlite_fields(cursor, table)
        model_name = _infer_model_name_from_table(table)
        models[model_name] = VirtualModelState(name=model_name, table=table, fields=fields)
    return models


def _inspect_legacy_sqlite_fields(cursor: Any, table: str) -> dict:
    cursor.execute(f"PRAGMA table_info(\"{table}\")")
    cols = cursor.fetchall()
    fields = {}
    for col in cols:
        col_name = col[1]
        col_type = col[2]
        nullable = col[3] == 0
        default = col[4]
        fields[col_name] = {
            "type": "String" if "text" in col_type.lower() else "Integer",
            "sql_type": col_type,
            "nullable": nullable,
            "default": default,
        }
    return fields


def _infer_model_name_from_table(table: str) -> str:
    parts = table.split('_')
    model_name = "".join(p.capitalize() for p in parts)
    if model_name.endswith('s'):
        model_name = model_name[:-1]
    if model_name.endswith('ie'):
        model_name = model_name[:-2] + 'y'
    return model_name


def _reconstruct_historical_state(mig_dir: str, mig_files: list[str]) -> Any:
    from asok.orm.migrations.state import ProjectState

    class_migrations, legacy_files = _classify_migrations(mig_dir, mig_files)

    if not legacy_files:
        state = ProjectState()
        _apply_class_migrations_to_state(state, class_migrations)
        return state

    conn = _run_legacy_in_memory(legacy_files)
    try:
        models = _inspect_legacy_sqlite_tables(conn)
    finally:
        conn.close()

    state = ProjectState(models)
    _apply_class_migrations_to_state(state, class_migrations)
    return state



def _build_declarative_migration_file_content(name: str, dependencies: list[str], operations: list[Any]) -> str:
    dep_list = ",\n        ".join(repr(d) for d in dependencies)
    op_list = ",\n        ".join(op.deconstruct() for op in operations)
    return f'''"""
Asok Migration: {name}
Generated at: {time.strftime("%Y-%m-%d %H:%M:%S")}
"""

from asok import Migration, operations

class Migration(Migration):
    dependencies = [
        {dep_list}
    ]

    operations = [
        {op_list}
    ]
'''


def _is_valid_migration_name(name) -> bool:
    return _is_valid_scaffold_name(name, "migration")


def _enter_project_root_for_migrations(root: str) -> None:
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)
    src_path = os.path.join(root, "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)


def _load_wsgi_for_migrations(root: str):
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")
    if not os.path.isfile(wsgi_path):
        return None
    try:
        spec = _ilu.spec_from_file_location("_wsgi_mig", wsgi_path)
        wsgi_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(wsgi_mod)
        return wsgi_mod
    except Exception as e:
        Style.warn(f"Failed to load wsgi.py: {e}")
        return None


def _scan_models_dir_for_migrations(root: str) -> None:
    model_dir = os.path.join(root, "src/models")
    if not os.path.isdir(model_dir):
        return
    for f in sorted(os.listdir(model_dir)):
        if not _is_loadable_model_filename(f):
            continue
        filepath = os.path.join(model_dir, f)
        if not os.path.abspath(filepath).startswith(os.path.abspath(model_dir)):
            continue
        _load_model_file_for_migrations(filepath, f)


def _is_loadable_model_filename(filename: str) -> bool:
    if _has_path_or_dunder(filename):
        return False
    return filename.endswith(".py") or filename.endswith(".pyc")


def _has_path_or_dunder(filename: str) -> bool:
    if ".." in filename or "/" in filename or "\\" in filename:
        return True
    return filename.startswith("__")


def _load_model_file_for_migrations(filepath: str, filename: str) -> None:
    ext_len = 4 if filename.endswith(".pyc") else 3
    mod_name = f"_mig_model_{filename[:-ext_len]}"
    try:
        spec = _ilu.spec_from_file_location(mod_name, filepath)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        Style.warn(f"Could not load model {filename}: {e}")


def _inject_admin_user_methods(wsgi_mod) -> None:
    if not wsgi_mod or not hasattr(wsgi_mod, "app"):
        return
    app = getattr(wsgi_mod, "app")
    if hasattr(app, "_admin"):
        app._admin._inject_user_methods()


def _announce_registered_models() -> None:
    if MODELS_REGISTRY:
        Style.info(f"Detected models: {', '.join(MODELS_REGISTRY.keys())}")
    else:
        Style.warn("No models registered. Check your model definitions.")


def _build_migration_sql(engine) -> tuple[list[str], list[str]]:
    from ..orm.engines import SQLiteEngine

    is_sqlite = isinstance(engine, SQLiteEngine)
    up_sql: list[str] = []
    down_sql: list[str] = []
    for model_name, model_cls in MODELS_REGISTRY.items():
        _build_model_migration_sql(engine, model_name, model_cls, up_sql, down_sql, is_sqlite)
    return up_sql, down_sql


def _build_model_migration_sql(engine, model_name, model_cls, up_sql, down_sql, is_sqlite) -> None:
    table = model_cls._table
    if not engine.table_exists(table):
        Style.info(f"  + New table detected: {table}")
        _emit_create_table(engine, model_cls, table, up_sql, down_sql)
    else:
        _emit_alter_table(engine, model_cls, table, up_sql, down_sql)
    _emit_pivot_tables(engine, model_name, model_cls, up_sql, down_sql)
    _emit_fts_changes(engine, model_cls, table, is_sqlite, up_sql, down_sql)


def _emit_create_table(engine, model_cls, table: str, up_sql, down_sql) -> None:
    fields = _build_create_table_columns(engine, model_cls)
    q_table = engine.quote_identifier(table)
    sql_create = f"CREATE TABLE IF NOT EXISTS {q_table} ({', '.join(fields)})"
    up_sql.append(f"conn.execute({repr(sql_create)})")
    down_sql.append(f"conn.execute({repr(f'DROP TABLE IF EXISTS {q_table}')})")


def _build_create_table_columns(engine, model_cls) -> list[str]:
    pk_def = getattr(engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT")
    fields: list[str] = []
    if "id" not in model_cls._fields:
        fields.append(pk_def)
    for f_name, f_obj in model_cls._fields.items():
        if f_name == "id":
            fields.append(pk_def)
            continue
        fields.append(_build_column_def(engine, f_name, f_obj, include_constraints=True))
    return fields


def _build_column_def(engine, f_name: str, f_obj, include_constraints: bool) -> str:
    col_type = engine.get_column_type(f_obj)
    q_f_name = engine.quote_identifier(f_name)
    col = f"{q_f_name} {col_type}"
    if include_constraints:
        if f_obj.unique:
            col += " UNIQUE"
        if not f_obj.nullable:
            col += " NOT NULL"
    if f_obj.default is not None:
        col += f" DEFAULT {_format_default_value(f_obj.default)}"
    return col


def _format_default_value(default) -> str:
    if isinstance(default, bool):
        return str(default).lower()
    if isinstance(default, (int, float)):
        return str(default)
    return f"'{default}'"


def _emit_alter_table(engine, model_cls, table: str, up_sql, down_sql) -> None:
    existing_cols = set(engine.get_table_columns(table))
    for f_name, f_obj in model_cls._fields.items():
        if f_name in existing_cols:
            continue
        Style.info(f"    + New column detected: {table}.{f_name}")
        col_sql = _build_column_def(engine, f_name, f_obj, include_constraints=False)
        q_table = engine.quote_identifier(table)
        up_sql.append(f"conn.execute({repr(f'ALTER TABLE {q_table} ADD COLUMN {col_sql}')})")
        down_sql.append(
            f"# Column drop depends on DB: cannot easily drop column {f_name} from {table}"
        )


def _emit_pivot_tables(engine, model_name, model_cls, up_sql, down_sql) -> None:
    processed_pivots: set[str] = set()
    for _, rel in model_cls._relations.items():
        if rel.type != "BelongsToMany":
            continue
        _emit_one_pivot(engine, model_name, rel, processed_pivots, up_sql, down_sql)


def _emit_one_pivot(engine, model_name: str, rel, processed_pivots, up_sql, down_sql) -> None:
    pivot, pfk, pofk = _resolve_pivot_identifiers(model_name, rel)
    if pivot in processed_pivots:
        return
    processed_pivots.add(pivot)
    if engine.table_exists(pivot):
        return
    Style.info(f"    + New pivot table detected: {pivot}")
    _emit_pivot_table_sql(engine, pivot, pfk, pofk, up_sql, down_sql)


def _resolve_pivot_identifiers(model_name: str, rel) -> tuple[str, str, str]:
    a = model_name.lower()
    b = rel.target_model_name.lower()
    pivot = rel.pivot_table or "_".join(sorted([a, b]))
    pfk = rel.pivot_fk or f"{a}_id"
    pofk = rel.pivot_other_fk or f"{b}_id"
    return pivot, pfk, pofk


def _emit_pivot_table_sql(engine, pivot: str, pfk: str, pofk: str, up_sql, down_sql) -> None:
    q_pivot = engine.quote_identifier(pivot)
    q_pfk = engine.quote_identifier(pfk)
    q_pofk = engine.quote_identifier(pofk)
    sql_pivot = (
        f"CREATE TABLE IF NOT EXISTS {q_pivot} ("
        f"{q_pfk} INTEGER NOT NULL, {q_pofk} INTEGER NOT NULL, "
        f"PRIMARY KEY ({q_pfk}, {q_pofk}))"
    )
    up_sql.append(f"conn.execute({repr(sql_pivot)})")
    down_sql.append(f"conn.execute({repr(f'DROP TABLE IF EXISTS {q_pivot}')})")


def _emit_fts_changes(engine, model_cls, table: str, is_sqlite: bool, up_sql, down_sql) -> None:
    if not model_cls._search_fields:
        return
    if is_sqlite:
        _emit_sqlite_fts(model_cls, table, engine, up_sql, down_sql)
    else:
        _emit_mysql_fulltext(model_cls, table, engine, up_sql, down_sql)


def _emit_sqlite_fts(model_cls, table: str, engine, up_sql, down_sql) -> None:
    fts_table = f"{table}_fts"
    if engine.table_exists(fts_table):
        return
    Style.info(f"    + New FTS table detected: {fts_table}")
    f_names = ", ".join(model_cls._search_fields)
    sql_fts = (
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table} USING fts5"
        f"({f_names}, content='{table}', content_rowid='id')"
    )
    up_sql.append(f"conn.execute({repr(sql_fts)})")
    up_sql.append(f"conn.execute({repr(f'INSERT INTO {fts_table}({fts_table}) VALUES(' + repr('rebuild') + ')')})")
    _emit_sqlite_fts_triggers(model_cls, table, fts_table, up_sql, down_sql)


def _emit_sqlite_fts_triggers(model_cls, table: str, fts_table: str, up_sql, down_sql) -> None:
    triggers = _build_sqlite_fts_triggers(model_cls, table, fts_table)
    for sql in triggers:
        up_sql.append(f"conn.execute({repr(sql)})")
    drop_table_sql = f'DROP TABLE IF EXISTS "{fts_table}"'
    down_sql.append(f"conn.execute({repr(drop_table_sql)})")
    for trigger in ("_ai", "_ad", "_au"):
        drop_trigger_sql = f'DROP TRIGGER IF EXISTS "{table}{trigger}"'
        down_sql.append(f"conn.execute({repr(drop_trigger_sql)})")


def _build_sqlite_fts_triggers(model_cls, table: str, fts_table: str) -> tuple[str, str, str]:
    f_quoted = ", ".join(f'"{n}"' for n in model_cls._search_fields)
    f_new = ", ".join(f'new."{n}"' for n in model_cls._search_fields)
    f_old = ", ".join(f'old."{n}"' for n in model_cls._search_fields)
    ai = (
        f'CREATE TRIGGER IF NOT EXISTS "{table}_ai" AFTER INSERT ON "{table}" BEGIN '
        f'INSERT INTO "{fts_table}"(rowid, {f_quoted}) VALUES (new.id, {f_new}); END;'
    )
    ad = (
        f'CREATE TRIGGER IF NOT EXISTS "{table}_ad" AFTER DELETE ON "{table}" BEGIN '
        f'INSERT INTO "{fts_table}"("{fts_table}", rowid, {f_quoted}) '
        f"VALUES('delete', old.id, {f_old}); END;"
    )
    au = (
        f'CREATE TRIGGER IF NOT EXISTS "{table}_au" AFTER UPDATE ON "{table}" BEGIN '
        f'INSERT INTO "{fts_table}"("{fts_table}", rowid, {f_quoted}) '
        f"VALUES('delete', old.id, {f_old}); "
        f'INSERT INTO "{fts_table}"(rowid, {f_quoted}) VALUES (new.id, {f_new}); END;'
    )
    return ai, ad, au


def _emit_mysql_fulltext(model_cls, table: str, engine, up_sql, down_sql) -> None:
    from ..orm.engines import MySQLEngine

    if not isinstance(engine, MySQLEngine):
        return
    index_name = f"idx_{table}_fts"
    cols = ", ".join(engine.quote_identifier(c) for c in model_cls._search_fields)
    q_table = engine.quote_identifier(table)
    q_index = engine.quote_identifier(index_name)
    idx_check = engine.execute(
        "SELECT COUNT(*) as cnt FROM information_schema.statistics "
        "WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?",
        (table, index_name),
    )
    if idx_check and idx_check[0].get("cnt", 0) != 0:
        return
    Style.info(f"    + New FULLTEXT index detected: {index_name}")
    up_sql.append(
        f"conn.execute({repr(f'ALTER TABLE {q_table} ADD FULLTEXT INDEX {q_index} ({cols})')})"
    )
    down_sql.append(
        f"conn.execute({repr(f'ALTER TABLE {q_table} DROP INDEX {q_index}')})"
    )





def make_page(name: str) -> None:
    """Generate a page directory with page.py and page.html."""
    if not _is_valid_page_name(name):
        return
    page_dir = f"src/pages/{name}"
    os.makedirs(page_dir, exist_ok=True)
    _ensure_init_py("src/pages")
    _ensure_init_py(page_dir)
    _write_page_py(page_dir)
    _write_page_html(page_dir, name)
    Style.success(f"Page created: {Style.BOLD}{page_dir}/...{Style.RESET}")


def _is_valid_page_name(name) -> bool:
    if not _check_name_basics(name, "page"):
        return False
    if ".." in name or "\\" in name:
        Style.error("Page name cannot contain '..' or backslashes")
        return False
    return _is_valid_page_components(name)


def _is_valid_page_components(name: str) -> bool:
    for part in name.split("/"):
        if not part or not part.replace("_", "").replace("-", "").isalnum():
            Style.error(
                f"Invalid page name component: '{part}' (must contain only letters, "
                "numbers, hyphens, and underscores)"
            )
            return False
    return True


def _write_page_py(page_dir: str) -> None:
    py_path = os.path.join(page_dir, "page.py")
    if os.path.exists(py_path):
        return
    with open(py_path, "w", encoding="utf-8") as f:
        f.write("""\
from asok import Request

def render(request: Request):
    return request.html('page.html')
""")


def _write_page_html(page_dir: str, name: str) -> None:
    html_path = os.path.join(page_dir, "page.html")
    if os.path.exists(html_path):
        return
    title = name.replace("/", " ").replace("-", " ").title()
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(f"""\
{{% extends "html/base.html" %}}

{{% block title %}}{title}{{% endblock %}}

{{% block main %}}
<div class="container page-header">
    <h1>{title}</h1>
</div>
{{% endblock %}}
""")


def make_component(name: str) -> None:
    """Generate a high-level UI component in src/components/."""
    if not _is_valid_scaffold_name(name, "component"):
        return
    os.makedirs("src/components", exist_ok=True)
    _ensure_init_py("src/components")
    filename = f"src/components/{name.lower()}.py"
    if os.path.exists(filename):
        print(f"  {filename} already exists.")
        return
    class_name = "".join(x.capitalize() for x in name.replace("-", "_").split("_"))
    content = f"""\
from asok.component import Component

class {class_name}(Component):
    \"\"\"Reusable UI component for {name}.\"\"\"

    def render(self) -> str:
        return self.html(\"\"\"
            <div class="{name.lower()}">
                <!-- Component Content -->
                <p>{name.capitalize()} Component</p>
            </div>
        \"\"\")
"""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    Style.success(f"Component created: {Style.BOLD}{filename}{Style.RESET}")
