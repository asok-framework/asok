from __future__ import annotations

import importlib.util as _ilu
import os
import sys
import time

from ..orm import MODELS_REGISTRY, Model, slugify
from .scaffold import _ensure_init_py
from .server import _find_project_root
from .style import Style


def make_model(name: str) -> None:
    """Generate a model file in src/models/.

    SECURITY: Validates name to prevent path traversal attacks.
    """
    # SECURITY: Validate model name to prevent path traversal
    if not name or not isinstance(name, str):
        Style.error("Invalid model name")
        return
    if len(name) > 100:
        Style.error("Model name too long (max 100 characters)")
        return
    # SECURITY: Only allow alphanumeric and underscores
    if not name.replace("_", "").replace("-", "").isalnum():
        Style.error("Model name must contain only letters, numbers, hyphens, and underscores")
        return
    # SECURITY: Prevent path traversal
    if ".." in name or "/" in name or "\\" in name:
        Style.error("Model name cannot contain path separators")
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
    """Generate a middleware file in src/middlewares/.

    SECURITY: Validates name to prevent path traversal attacks.
    """
    # SECURITY: Validate middleware name to prevent path traversal
    if not name or not isinstance(name, str):
        Style.error("Invalid middleware name")
        return
    if len(name) > 100:
        Style.error("Middleware name too long (max 100 characters)")
        return
    # SECURITY: Only allow alphanumeric and underscores
    if not name.replace("_", "").replace("-", "").isalnum():
        Style.error("Middleware name must contain only letters, numbers, hyphens, and underscores")
        return
    # SECURITY: Prevent path traversal
    if ".." in name or "/" in name or "\\" in name:
        Style.error("Middleware name cannot contain path separators")
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
    # SECURITY: Validate migration name to prevent path traversal
    if not name or not isinstance(name, str):
        Style.error("Invalid migration name")
        return
    if len(name) > 100:
        Style.error("Migration name too long (max 100 characters)")
        return
    # SECURITY: Only allow alphanumeric, underscores, and hyphens
    if not name.replace("_", "").replace("-", "").isalnum():
        Style.error("Migration name must contain only letters, numbers, hyphens, and underscores")
        return
    # SECURITY: Prevent path traversal
    if ".." in name or "/" in name or "\\" in name:
        Style.error("Migration name cannot contain path separators")
        return

    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project.")
        return
    os.chdir(root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    # Add project to sys.path
    if root not in sys.path:
        sys.path.insert(0, root)
    if "src" not in sys.path:
        sys.path.insert(0, os.path.join(root, "src"))

    # Load models. Priority: wsgi.py, then src/models/
    Style.info("Analyzing project models...")

    # 1. Load wsgi.py/c (often imports all models)
    wsgi_path = os.path.join(root, "wsgi.py")
    if not os.path.isfile(wsgi_path):
        wsgi_path = os.path.join(root, "wsgi.pyc")

    wsgi_mod = None
    if os.path.isfile(wsgi_path):
        try:
            spec = _ilu.spec_from_file_location("_wsgi_mig", wsgi_path)
            wsgi_mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(wsgi_mod)
        except Exception as e:
            Style.warn(f"Failed to load wsgi.py: {e}")

    # 2. Scan src/models/ for any missed models
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
                    Style.warn(f"Could not load model {f}: {e}")

    # 3. Ensure Admin relations are injected if Admin is present
    if wsgi_mod and hasattr(wsgi_mod, "app"):
        app = getattr(wsgi_mod, "app")
        if hasattr(app, "_admin"):
            app._admin._inject_user_methods()

    # Ensure migration dir exists
    mig_dir = os.path.join(root, "src/migrations")
    os.makedirs(mig_dir, exist_ok=True)
    _ensure_init_py(mig_dir)

    # Database connection
    if MODELS_REGISTRY:
        Style.info(f"Detected models: {', '.join(MODELS_REGISTRY.keys())}")
    else:
        Style.warn("No models registered. Check your model definitions.")
    engine = Model.get_engine()
    from ..orm.engines import SQLiteEngine
    is_sqlite = isinstance(engine, SQLiteEngine)

    # Analysis
    up_sql = []
    down_sql = []

    for model_name, model_cls in MODELS_REGISTRY.items():
        table = model_cls._table

        # Check if table exists
        exists = engine.table_exists(table)

        if not exists:
            Style.info(f"  + New table detected: {table}")
            # Generate CREATE TABLE
            fields = []

            # Ensure 'id' is always the first column if not explicitly defined
            pk_def = getattr(engine, "primary_key_def", "id INTEGER PRIMARY KEY AUTOINCREMENT")
            if "id" not in model_cls._fields:
                fields.append(pk_def)

            for f_name, f_obj in model_cls._fields.items():
                if f_name == "id":
                    fields.append(pk_def)
                else:
                    col_type = engine.get_column_type(f_obj)
                    col = f"{f_name} {col_type}"
                    if f_obj.unique:
                        col += " UNIQUE"
                    if not f_obj.nullable:
                        col += " NOT NULL"
                    if f_obj.default is not None:
                        if isinstance(f_obj.default, (int, float)):
                            col += f" DEFAULT {f_obj.default}"
                        elif isinstance(f_obj.default, bool):
                            col += f" DEFAULT {str(f_obj.default).lower()}"
                        else:
                            col += f" DEFAULT '{f_obj.default}'"
                    fields.append(col)

            q_table = engine.quote_identifier(table)
            sql_create = f"CREATE TABLE IF NOT EXISTS {q_table} ({', '.join(fields)})"
            up_sql.append(f"conn.execute({repr(sql_create)})")
            down_sql.append(f"conn.execute({repr(f'DROP TABLE IF EXISTS {q_table}')})")
        else:
            # Check for new columns
            existing_cols = set(engine.get_table_columns(table))
            for f_name, f_obj in model_cls._fields.items():
                if f_name not in existing_cols:
                    Style.info(f"    + New column detected: {table}.{f_name}")
                    col_type = engine.get_column_type(f_obj)
                    col_sql = f"{f_name} {col_type}"
                    if f_obj.default is not None:
                        if isinstance(f_obj.default, (int, float)):
                            col_sql += f" DEFAULT {f_obj.default}"
                        elif isinstance(f_obj.default, bool):
                            col_sql += f" DEFAULT {str(f_obj.default).lower()}"
                        else:
                            col_sql += f" DEFAULT '{f_obj.default}'"

                    q_table = engine.quote_identifier(table)
                    sql_alter = f"ALTER TABLE {q_table} ADD COLUMN {col_sql}"
                    up_sql.append(f"conn.execute({repr(sql_alter)})")
                    down_sql.append(
                        f"# Column drop depends on DB: cannot easily drop column {f_name} from {table}"
                    )

        # Check for BelongsToMany pivot tables
        processed_pivots = set()
        for rel_name, rel in model_cls._relations.items():
            if rel.type == "BelongsToMany":
                # Logic from Model._pivot_info
                a = model_name.lower()
                b = rel.target_model_name.lower()
                pivot = rel.pivot_table or "_".join(sorted([a, b]))
                if pivot in processed_pivots:
                    continue
                processed_pivots.add(pivot)

                exists = engine.table_exists(pivot)

                if not exists:
                    Style.info(f"    + New pivot table detected: {pivot}")
                    pfk = rel.pivot_fk or f"{a}_id"
                    pofk = rel.pivot_other_fk or f"{b}_id"

                    q_pivot = engine.quote_identifier(pivot)
                    q_pfk = engine.quote_identifier(pfk)
                    q_pofk = engine.quote_identifier(pofk)

                    sql_pivot = (
                        f"CREATE TABLE IF NOT EXISTS {q_pivot} ("
                        f"{q_pfk} INTEGER NOT NULL, "
                        f"{q_pofk} INTEGER NOT NULL, "
                        f"PRIMARY KEY ({q_pfk}, {q_pofk}))"
                    )
                    up_sql.append(f"conn.execute({repr(sql_pivot)})")
                    down_sql.append(
                        f"conn.execute({repr(f'DROP TABLE IF EXISTS {q_pivot}')})"
                    )

        # Check for FTS tables/indexes
        if model_cls._search_fields:
            if is_sqlite:
                # SQLite: FTS5 virtual table + triggers
                fts_table = f"{table}_fts"
                fts_exists = engine.table_exists(fts_table)
                if not fts_exists:
                    Style.info(f"    + New FTS table detected: {fts_table}")
                    f_names = ", ".join(model_cls._search_fields)
                    sql_fts = f"CREATE VIRTUAL TABLE IF NOT EXISTS {fts_table} USING fts5({f_names}, content='{table}', content_rowid='id')"
                    up_sql.append(f"conn.execute({repr(sql_fts)})")

                    sql_rebuild = f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')"
                    up_sql.append(f"conn.execute({repr(sql_rebuild)})")

                    # Triggers to keep FTS in sync
                    f_quoted = ", ".join([f'"{n}"' for n in model_cls._search_fields])
                    f_new = ", ".join([f'new."{n}"' for n in model_cls._search_fields])
                    f_old = ", ".join([f'old."{n}"' for n in model_cls._search_fields])

                    ai = f'CREATE TRIGGER IF NOT EXISTS "{table}_ai" AFTER INSERT ON "{table}" BEGIN INSERT INTO "{fts_table}"(rowid, {f_quoted}) VALUES (new.id, {f_new}); END;'
                    ad = f'CREATE TRIGGER IF NOT EXISTS "{table}_ad" AFTER DELETE ON "{table}" BEGIN INSERT INTO "{fts_table}"("{fts_table}", rowid, {f_quoted}) VALUES(\'delete\', old.id, {f_old}); END;'
                    au = f'CREATE TRIGGER IF NOT EXISTS "{table}_au" AFTER UPDATE ON "{table}" BEGIN INSERT INTO "{fts_table}"("{fts_table}", rowid, {f_quoted}) VALUES(\'delete\', old.id, {f_old}); INSERT INTO "{fts_table}"(rowid, {f_quoted}) VALUES (new.id, {f_new}); END;'

                    up_sql.append(f"conn.execute({repr(ai)})")
                    up_sql.append(f"conn.execute({repr(ad)})")
                    up_sql.append(f"conn.execute({repr(au)})")

                    sql_drop_fts = f'DROP TABLE IF EXISTS "{fts_table}"'
                    down_sql.append(f"conn.execute({repr(sql_drop_fts)})")

                    sql_ai = f'DROP TRIGGER IF EXISTS "{table}_ai"'
                    sql_ad = f'DROP TRIGGER IF EXISTS "{table}_ad"'
                    sql_au = f'DROP TRIGGER IF EXISTS "{table}_au"'

                    down_sql.append(f"conn.execute({repr(sql_ai)})")
                    down_sql.append(f"conn.execute({repr(sql_ad)})")
                    down_sql.append(f"conn.execute({repr(sql_au)})")
            else:
                # MySQL/Postgres: FULLTEXT INDEX via ALTER TABLE
                from ..orm.engines import MySQLEngine
                if isinstance(engine, MySQLEngine):
                    index_name = f"idx_{table}_fts"
                    cols = ", ".join([engine.quote_identifier(c) for c in model_cls._search_fields])
                    q_table = engine.quote_identifier(table)
                    q_index = engine.quote_identifier(index_name)
                    # Check if FULLTEXT index already exists (use ? so translate_query handles dialect)
                    idx_check = engine.execute(
                        "SELECT COUNT(*) as cnt FROM information_schema.statistics "
                        "WHERE table_schema = DATABASE() AND table_name = ? AND index_name = ?",
                        (table, index_name),
                    )
                    if not idx_check or idx_check[0].get("cnt", 0) == 0:
                        Style.info(f"    + New FULLTEXT index detected: {index_name}")
                        sql_ft = f"ALTER TABLE {q_table} ADD FULLTEXT INDEX {q_index} ({cols})"
                        sql_drop_ft = f"ALTER TABLE {q_table} DROP INDEX {q_index}"
                        up_sql.append(f"conn.execute({repr(sql_ft)})")
                        down_sql.append(f"conn.execute({repr(sql_drop_ft)})")

    if not up_sql:
        Style.info("No changes detected in models.")
        return

    # Generate file
    # SECURITY: Validate filenames to prevent directory traversal
    all_files = os.listdir(mig_dir)
    existing = []
    for f in all_files:
        if ".." in f or "/" in f or "\\" in f:
            continue
        if f.endswith(".py") and f[:4].isdigit():
            existing.append(f)

    next_num = max([int(f[:4]) for f in existing] + [0]) + 1
    # SECURITY: slugify() sanitizes the name, but validate the result
    slug = slugify(name)
    if ".." in slug or "/" in slug or "\\" in slug:
        Style.error("Invalid migration name after slugification")
        return
    filename = f"{next_num:04d}_{slug}.py"
    filepath = os.path.join(mig_dir, filename)

    # SECURITY: Final verification that filepath is within mig_dir
    if not os.path.abspath(filepath).startswith(os.path.abspath(mig_dir)):
        Style.error("Security error: invalid migration path")
        return

    up_joined = "\n    ".join(up_sql)
    down_joined = "\n    ".join(reversed(down_sql))
    content = f'''"""
Asok Migration: {name}
Generated at: {time.strftime("%Y-%m-%d %H:%M:%S")}
"""

def up(conn):
    """Apply changes."""
    {up_joined}

def down(conn):
    """Revert changes."""
    {down_joined}
'''
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    Style.success(f"Created migration: {Style.BOLD}{filename}{Style.RESET}")


def make_page(name: str) -> None:
    """Generate a page directory with page.py and page.html.

    SECURITY: Validates name to prevent path traversal attacks.
    """
    # SECURITY: Validate page name to prevent path traversal
    if not name or not isinstance(name, str):
        Style.error("Invalid page name")
        return
    if len(name) > 100:
        Style.error("Page name too long (max 100 characters)")
        return
    # SECURITY: Allow forward slashes for nested pages, but prevent traversal
    if ".." in name or "\\" in name:
        Style.error("Page name cannot contain '..' or backslashes")
        return
    # SECURITY: Validate each path component
    parts = name.split("/")
    for part in parts:
        if not part or not part.replace("_", "").replace("-", "").isalnum():
            Style.error(f"Invalid page name component: '{part}' (must contain only letters, numbers, hyphens, and underscores)")
            return

    page_dir = f"src/pages/{name}"
    os.makedirs(page_dir, exist_ok=True)
    _ensure_init_py("src/pages")
    _ensure_init_py(page_dir)

    py_path = os.path.join(page_dir, "page.py")
    if not os.path.exists(py_path):
        with open(py_path, "w", encoding="utf-8") as f:
            f.write("""\
from asok import Request

def render(request: Request):
    return request.html('page.html')
""")

    html_path = os.path.join(page_dir, "page.html")
    if not os.path.exists(html_path):
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

    Style.success(f"Page created: {Style.BOLD}{page_dir}/...{Style.RESET}")


def make_component(name: str) -> None:
    """Generate a high-level UI component in src/components/.

    SECURITY: Validates name to prevent path traversal attacks.
    """
    # SECURITY: Validate component name to prevent path traversal
    if not name or not isinstance(name, str):
        Style.error("Invalid component name")
        return
    if len(name) > 100:
        Style.error("Component name too long (max 100 characters)")
        return
    # SECURITY: Only allow alphanumeric, underscores, and hyphens
    if not name.replace("_", "").replace("-", "").isalnum():
        Style.error("Component name must contain only letters, numbers, hyphens, and underscores")
        return
    # SECURITY: Prevent path traversal
    if ".." in name or "/" in name or "\\" in name:
        Style.error("Component name cannot contain path separators")
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
