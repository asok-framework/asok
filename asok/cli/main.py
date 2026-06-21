from __future__ import annotations

import argparse
import os
import sys

from .. import __version__
from .build import run_build
from .database import (
    run_createsuperuser,
    run_db_command,
    run_dumpdata,
    run_loaddata,
    run_migrate,
    run_seed,
)
from .deploy import run_deploy
from .generators import (
    make_component,
    make_middleware,
    make_migration,
    make_model,
    make_page,
)
from .runner import run_routes, run_shell, run_test
from .scaffold import scaffold
from .server import _find_project_root, run_dev, run_preview
from .style import Style
from .tools import (
    admin_enable,
    assets_install,
    assets_minify,
    graphql_install,
    image_enable,
    image_install,
    image_optimize_all,
    tailwind_build,
    tailwind_enable,
    tailwind_install,
)


def print_help() -> None:
    """Custom professional help display for Asok."""
    print(
        f"\n{Style.BOLD}{Style.CYAN}ASOK FRAMEWORK{Style.RESET} {Style.DIM}v{__version__}{Style.RESET}"
    )
    print("Minimalist Python Web Framework — minimal external dependencies\n")

    print(f"{Style.BOLD}{Style.BLUE}Usage:{Style.RESET}")
    print("  asok <command> [options]\n")

    groups = {
        "Scaffolding": [
            ("create", "Create a new Asok project"),
            ("make page", "Create a new page (py + html)"),
            ("make component", "Create a new reusable UI component"),
            ("make model", "Create a new database model"),
            ("make migration", "Create a new database migration"),
            ("make middleware", "Create a new middleware"),
        ],
        "Development": [
            ("dev", "Start the development server with hot-reload"),
            ("worker", "Start the background task processing worker"),
            ("preview", "Start the production-ready server locally"),
            ("shell", "Open an interactive Python shell with app context"),
            ("routes", "Display all registered routes"),
            ("test", "Run the project's test suite"),
        ],
        "Database": [
            (
                "migrate",
                "Apply pending migrations (--rollback, --status, --to, --steps, --reset)",
            ),
            ("db schema", "Display database tables and schema details"),
            ("db explain", "Analyze SQL query performance using explain plan"),
            ("seed", "Run database seeders"),
            ("createsuperuser", "Create or update an administrative user"),
            ("dumpdata", "Dump database records to a JSON fixture file"),
            ("loaddata", "Load records from a JSON fixture file"),
        ],
        "Tools": [
            ("tailwind", "Manage Tailwind CSS (install/build/enable)"),
            ("admin", "Manage Admin interface (enable)"),
            ("image", "Manage Image Optimization (install/enable/optimize)"),
            ("assets", "Manage JS/CSS assets (install/minify)"),
            ("graphql", "Manage GraphQL playground assets (install)"),
            (
                "deploy",
                "Generate production deployment configs (Gunicorn/Nginx/SystemD)",
            ),
            (
                "build",
                "Generate an optimized production build (--with-db, --keep-source, -o)",
            ),
        ],
    }

    for group, commands in groups.items():
        print(f"{Style.BOLD}{Style.BLUE}{group}:{Style.RESET}")
        for cmd, help_text in commands:
            print(
                f"  {Style.GREEN}{cmd:<15}{Style.RESET} {Style.DIM}{help_text}{Style.RESET}"
            )
        print()


def _add_virtualenv_to_path(root: str | None) -> None:
    """Detect local or active virtual environments and add their site-packages to sys.path."""
    venv_paths = _discover_venv_paths(root)
    for venv_path in venv_paths:
        _add_venv_site_packages_to_path(venv_path)


def _discover_venv_paths(root: str | None) -> list[str]:
    venv_paths: list[str] = []
    active_venv = os.environ.get("VIRTUAL_ENV")
    if active_venv:
        venv_paths.append(active_venv)
    if root:
        venv_paths.extend(_find_local_venvs(root, venv_paths))
    return venv_paths


def _find_local_venvs(root: str, existing: list[str]) -> list[str]:
    found: list[str] = []
    for folder in (".venv", "venv", "env"):
        p = os.path.join(root, folder)
        if _is_acceptable_venv(p, existing, found):
            found.append(p)
    return found


def _is_acceptable_venv(p: str, existing: list[str], found: list[str]) -> bool:
    if p in existing or p in found:
        return False
    return os.path.isdir(p) and _looks_like_venv(p)


def _looks_like_venv(p: str) -> bool:
    return os.path.isdir(os.path.join(p, "lib")) or os.path.isdir(os.path.join(p, "Lib"))


def _add_venv_site_packages_to_path(venv_path: str) -> None:
    _add_unix_site_packages(venv_path)
    win_site = os.path.join(venv_path, "Lib", "site-packages")
    if os.path.isdir(win_site) and win_site not in sys.path:
        sys.path.insert(0, win_site)


def _add_unix_site_packages(venv_path: str) -> None:
    lib_path = os.path.join(venv_path, "lib")
    if not os.path.isdir(lib_path):
        return
    for item in os.listdir(lib_path):
        if item.startswith("python"):
            _prepend_site_packages(os.path.join(lib_path, item, "site-packages"))


def _prepend_site_packages(site_path: str) -> None:
    if os.path.isdir(site_path) and site_path not in sys.path:
        sys.path.insert(0, site_path)


def load_extension_commands(subparsers: argparse._SubParsersAction) -> None:
    """Load CLI commands registered by third-party extensions via entry points."""
    metadata = _import_metadata()
    try:
        entry_points = _select_command_entry_points(metadata)
    except Exception:
        return
    for ep in entry_points:
        _load_extension_entry_point(ep, subparsers)


def _import_metadata():
    if sys.version_info >= (3, 10):
        from importlib import metadata
        return metadata
    try:
        import importlib_metadata as metadata  # type: ignore
    except ImportError:
        from importlib import metadata
    return metadata


def _select_command_entry_points(metadata):
    eps = metadata.entry_points()
    if hasattr(eps, "select"):
        return eps.select(group="asok.commands")
    return eps.get("asok.commands", [])


def _load_extension_entry_point(ep, subparsers) -> None:
    try:
        register_func = ep.load()
        register_func(subparsers)
    except Exception as e:
        print(f"Warning: Failed to load CLI command from extension {ep.name}: {e}")


def main() -> None:
    """Terminal entry point for the 'asok' CLI."""
    root = _find_project_root()
    _add_virtualenv_to_path(root)
    if root:
        _load_dotenv_file(root)
        _sync_orm_db_path()
    os.environ["ASOK_CLI"] = "true"
    if _handle_pre_parse_flags():
        return
    parser, sub_parsers = _build_arg_parser()
    args = parser.parse_args()
    _dispatch_command(args, sub_parsers)


def _load_dotenv_file(root: str) -> None:
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    if os.path.getsize(env_path) > 1_000_000:
        print("Warning: .env file too large, skipping")
        return
    with open(env_path) as f:
        for i, line in enumerate(f):
            if i >= 10_000:
                break
            _apply_dotenv_line(line)


def _apply_dotenv_line(raw_line: str) -> None:
    parsed = _parse_dotenv_line(raw_line)
    if parsed is None:
        return
    k, v = parsed
    if _is_valid_env_name(k):
        os.environ[k] = v[:10_000]


def _parse_dotenv_line(raw_line: str):
    line = raw_line.strip()
    if _is_skip_dotenv_line(line):
        return None
    parts = line.split("=", 1)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def _is_skip_dotenv_line(line: str) -> bool:
    if not line or line.startswith("#") or "=" not in line:
        return True
    return len(line) > 10_000


def _is_valid_env_name(k: str) -> bool:
    return bool(k) and k.replace("_", "").isalnum() and len(k) <= 200


def _sync_orm_db_path() -> None:
    if "DATABASE_URL" not in os.environ:
        return
    from ..orm import Model

    Model._db_path = (os.environ["DATABASE_URL"] or "").strip() or None


def _handle_pre_parse_flags() -> bool:
    if _is_help_invocation():
        print_help()
        return True
    if _is_version_invocation():
        print(f"Asok Framework v{__version__}")
        return True
    return False


def _is_help_invocation() -> bool:
    return len(sys.argv) == 1 or "-h" in sys.argv or "--help" in sys.argv


def _is_version_invocation() -> bool:
    return "-v" in sys.argv or "--version" in sys.argv


def _build_arg_parser() -> tuple[argparse.ArgumentParser, dict]:
    parser = argparse.ArgumentParser(description="Asok Framework CLI", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    subparsers = parser.add_subparsers(dest="command")
    load_extension_commands(subparsers)
    sub_parsers = _register_subparsers(subparsers)
    return parser, sub_parsers


def _register_subparsers(subparsers) -> dict:
    sub_parsers = {}
    _register_basic_commands(subparsers, sub_parsers)
    _register_tooling_commands(subparsers, sub_parsers)
    _register_database_commands(subparsers)
    _register_make_command(subparsers)
    return sub_parsers


def _register_basic_commands(subparsers, sub_parsers: dict) -> None:
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("name")
    create_parser.add_argument("--tailwind", action="store_true", default=None)
    create_parser.add_argument("--admin", action="store_true", default=None)
    create_parser.add_argument("--image", action="store_true", default=None)
    cs_parser = subparsers.add_parser("createsuperuser")
    cs_parser.add_argument("--email", default=None)
    cs_parser.add_argument("--password", default=None)
    dev_parser = subparsers.add_parser("dev")
    dev_parser.add_argument("--port", type=int, default=8000)
    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("--port", type=int, default=8000)
    subparsers.add_parser("seed")
    subparsers.add_parser("routes")
    subparsers.add_parser("shell")
    subparsers.add_parser("test").add_argument("path", nargs="?", default=None)
    worker_parser = subparsers.add_parser("worker")
    worker_parser.add_argument(
        "action", nargs="?", choices=["run", "status"], default="run",
        help="'run' (default) starts the worker, 'status' shows queue status.",
    )


def _register_tooling_commands(subparsers, sub_parsers: dict) -> None:
    tw_parser = subparsers.add_parser("tailwind")
    tw_group = tw_parser.add_mutually_exclusive_group()
    tw_group.add_argument("--install", action="store_true")
    tw_group.add_argument("--build", action="store_true")
    tw_group.add_argument("--enable", action="store_true")
    tw_parser.add_argument("--minify", action="store_true")
    sub_parsers["tailwind"] = tw_parser

    admin_parser = subparsers.add_parser("admin")
    admin_parser.add_argument("--enable", action="store_true")
    sub_parsers["admin"] = admin_parser

    image_parser = subparsers.add_parser("image")
    image_parser.add_argument("--install", action="store_true")
    image_parser.add_argument("--enable", action="store_true")
    image_parser.add_argument("--optimize", action="store_true")
    image_parser.add_argument("--delete-originals", action="store_true")
    sub_parsers["image"] = image_parser

    assets_parser = subparsers.add_parser("assets")
    assets_parser.add_argument("--install", action="store_true")
    assets_parser.add_argument("--minify", action="store_true")
    sub_parsers["assets"] = assets_parser

    graphql_parser = subparsers.add_parser("graphql")
    graphql_parser.add_argument("--install", action="store_true", help="Download GraphiQL playground assets for offline use")
    sub_parsers["graphql"] = graphql_parser

    deploy_parser = subparsers.add_parser("deploy")
    deploy_parser.add_argument(
        "--prod-dir", default=None,
        help="Target directory on the production server (defaults to /var/www/<app_name>)",
    )
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--keep-source", action="store_true")
    build_parser.add_argument("--output", "-o", default=None)
    build_parser.add_argument("--with-db", action="store_true")


def _register_database_commands(subparsers) -> None:
    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--rollback", action="store_true")
    migrate_parser.add_argument("--status", action="store_true")
    migrate_parser.add_argument("--fake", action="store_true")
    migrate_parser.add_argument("--database", default=None)
    migrate_parser.add_argument("--to", default=None)
    migrate_parser.add_argument("--steps", type=int, default=None)
    migrate_parser.add_argument("--reset", action="store_true")
    db_parser = subparsers.add_parser("db")
    db_subparsers = db_parser.add_subparsers(dest="db_command")
    db_schema_parser = db_subparsers.add_parser("schema")
    db_schema_parser.add_argument("--database", default=None)
    db_explain_parser = db_subparsers.add_parser("explain")
    db_explain_parser.add_argument("query")
    db_explain_parser.add_argument("--database", default=None)
    dumpdata_parser = subparsers.add_parser("dumpdata")
    dumpdata_parser.add_argument("model", nargs="?", default=None)
    dumpdata_parser.add_argument("--output", default=None)
    loaddata_parser = subparsers.add_parser("loaddata")
    loaddata_parser.add_argument("file")


def _register_make_command(subparsers) -> None:
    make_parser = subparsers.add_parser("make")
    make_parser.add_argument(
        "type", choices=["model", "middleware", "page", "component", "migration"]
    )
    make_parser.add_argument("name", nargs="?", default=None)


def _dispatch_command(args, sub_parsers: dict) -> None:
    handler = _COMMAND_HANDLERS.get(args.command)
    if handler is not None:
        handler(args, sub_parsers)
        return
    # Extension commands register their handler via `parser.set_defaults(func=...)`.
    func = getattr(args, "func", None)
    if callable(func):
        func(args)
        return
    print_help()


def _require_project_root() -> str | None:
    root = _find_project_root()
    if not root:
        Style.error("Not inside an Asok project (no wsgi.py/c found).")
    return root


def _cmd_create(args, sub_parsers) -> None:
    scaffold(args.name, tailwind=args.tailwind, admin=args.admin, image=args.image)


def _cmd_createsuperuser(args, sub_parsers) -> None:
    run_createsuperuser(args.email, args.password)


def _cmd_tailwind(args, sub_parsers) -> None:
    root = _require_project_root()
    if not root:
        return
    try:
        _run_tailwind_subcommand(args, sub_parsers["tailwind"], root)
    except RuntimeError as e:
        Style.error(str(e))
        sys.exit(1)


def _run_tailwind_subcommand(args, tw_parser, root: str) -> None:
    if args.enable:
        tailwind_enable(root)
        return
    if args.install:
        tailwind_install(root, verbose=True)
        return
    if args.build:
        _run_tailwind_build(args, root)
        return
    tw_parser.print_help()


def _run_tailwind_build(args, root: str) -> None:
    from .server import _project_uses_tailwind

    if not _project_uses_tailwind(root):
        Style.warn("This project doesn't use Tailwind.")
        print("  To enable it, run: asok tailwind --enable")
        return
    tailwind_build(root, minify=args.minify)


def _cmd_admin(args, sub_parsers) -> None:
    root = _require_project_root()
    if not root:
        return
    if args.enable:
        admin_enable(root)
    else:
        sub_parsers["admin"].print_help()


def _cmd_image(args, sub_parsers) -> None:
    root = _require_project_root()
    if not root:
        return
    if args.enable:
        image_enable(root)
    elif args.install:
        image_install(root)
    elif args.optimize:
        image_optimize_all(root, delete_originals=args.delete_originals)
    else:
        sub_parsers["image"].print_help()


def _cmd_assets(args, sub_parsers) -> None:
    root = _require_project_root()
    if not root:
        return
    if args.install:
        assets_install(root)
    elif args.minify:
        assets_minify(root)
    else:
        sub_parsers["assets"].print_help()


def _cmd_graphql(args, sub_parsers) -> None:
    if args.install:
        try:
            graphql_install()
        except RuntimeError as e:
            Style.error(str(e))
    else:
        sub_parsers["graphql"].print_help()


def _cmd_deploy(args, sub_parsers) -> None:
    root = _require_project_root()
    if root:
        run_deploy(root, prod_dir=args.prod_dir)


def _cmd_build(args, sub_parsers) -> None:
    root = _require_project_root()
    if root:
        run_build(root, keep_source=args.keep_source, with_db=args.with_db, output=args.output)


def _cmd_dev(args, sub_parsers) -> None:
    run_dev(args.port)


def _cmd_preview(args, sub_parsers) -> None:
    run_preview(args.port)


def _cmd_migrate(args, sub_parsers) -> None:
    run_migrate(
        rollback=args.rollback, status=args.status, fake=args.fake,
        database=args.database, to_migration=args.to,
        steps=args.steps, reset=args.reset,
    )


def _cmd_db(args, sub_parsers) -> None:
    run_db_command(args)


def _cmd_dumpdata(args, sub_parsers) -> None:
    run_dumpdata(model_name=args.model, output_file=args.output)


def _cmd_loaddata(args, sub_parsers) -> None:
    run_loaddata(file_path=args.file)


def _cmd_seed(args, sub_parsers) -> None:
    run_seed()


def _cmd_routes(args, sub_parsers) -> None:
    run_routes()


def _cmd_shell(args, sub_parsers) -> None:
    run_shell()


def _cmd_test(args, sub_parsers) -> None:
    run_test(args.path)


def _cmd_worker(args, sub_parsers) -> None:
    from .worker import run_worker

    run_worker(action=args.action)


def _cmd_make(args, sub_parsers) -> None:
    if args.type == "migration":
        make_migration(args.name or "auto_migration")
        return
    if not args.name:
        Style.error(f"Please provide a name for the {args.type}.")
        sys.exit(1)
    _MAKE_HANDLERS[args.type](args.name)


_MAKE_HANDLERS = {
    "model": make_model,
    "middleware": make_middleware,
    "page": make_page,
    "component": make_component,
}


_COMMAND_HANDLERS = {
    "create": _cmd_create,
    "createsuperuser": _cmd_createsuperuser,
    "tailwind": _cmd_tailwind,
    "admin": _cmd_admin,
    "image": _cmd_image,
    "assets": _cmd_assets,
    "graphql": _cmd_graphql,
    "deploy": _cmd_deploy,
    "build": _cmd_build,
    "dev": _cmd_dev,
    "preview": _cmd_preview,
    "migrate": _cmd_migrate,
    "db": _cmd_db,
    "dumpdata": _cmd_dumpdata,
    "loaddata": _cmd_loaddata,
    "seed": _cmd_seed,
    "routes": _cmd_routes,
    "shell": _cmd_shell,
    "test": _cmd_test,
    "worker": _cmd_worker,
    "make": _cmd_make,
}


if __name__ == "__main__":
    main()
