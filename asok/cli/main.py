from __future__ import annotations

import argparse
import os
import sys

from .. import __version__
from .build import run_build
from .database import run_createsuperuser, run_migrate, run_seed
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
            ("preview", "Start the production-ready server locally"),
            ("shell", "Open an interactive Python shell with app context"),
            ("routes", "Display all registered routes"),
            ("test", "Run the project's test suite"),
        ],
        "Database": [
            ("migrate", "Apply pending migrations (--rollback, --status)"),
            ("seed", "Run database seeders"),
            ("createsuperuser", "Create or update an administrative user"),
        ],
        "Tools": [
            ("tailwind", "Manage Tailwind CSS (install/build/enable)"),
            ("admin", "Manage Admin interface (enable)"),
            ("image", "Manage Image Optimization (install/enable/optimize)"),
            ("assets", "Manage JS/CSS assets (install/minify)"),
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


def main() -> None:
    """Terminal entry point for the 'asok' CLI."""
    # Load .env early so that all components (like ORM) see the environment
    root = _find_project_root()
    if root:
        env_path = os.path.join(root, ".env")
        if os.path.exists(env_path):
            # SECURITY: Validate .env file size to prevent DoS
            if os.path.getsize(env_path) > 1_000_000:  # 1MB limit
                print("Warning: .env file too large, skipping")
            else:
                with open(env_path) as f:
                    line_num = 0
                    for line in f:
                        line_num += 1
                        # SECURITY: Limit lines processed to prevent DoS
                        if line_num > 10_000:
                            break
                        line = line.strip()
                        # SECURITY: Validate line length
                        if len(line) > 10_000:
                            continue
                        if line and not line.startswith("#") and "=" in line:
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                k, v = parts
                                k = k.strip()
                                v = v.strip()
                                # SECURITY: Validate environment variable name
                                if k and k.replace("_", "").isalnum() and len(k) <= 200:
                                    os.environ[k] = v[:10_000]  # Limit value length

        # Sync ORM DB path if DATABASE_URL is set in .env
        if "DATABASE_URL" in os.environ:
            from ..orm import Model

            Model._db_path = os.environ["DATABASE_URL"]

    os.environ["ASOK_CLI"] = "true"
    parser = argparse.ArgumentParser(description="Asok Framework CLI", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    subparsers = parser.add_subparsers(dest="command")

    # Command definitions
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("name")
    create_parser.add_argument("--tailwind", action="store_true", default=None)
    create_parser.add_argument("--admin", action="store_true", default=None)
    create_parser.add_argument("--image", action="store_true", default=None)

    cs_parser = subparsers.add_parser("createsuperuser")
    cs_parser.add_argument("--email", default=None)
    cs_parser.add_argument("--password", default=None)

    tw_parser = subparsers.add_parser("tailwind")
    tw_group = tw_parser.add_mutually_exclusive_group()
    tw_group.add_argument("--install", action="store_true")
    tw_group.add_argument("--build", action="store_true")
    tw_group.add_argument("--enable", action="store_true")
    tw_parser.add_argument("--minify", action="store_true")

    admin_parser = subparsers.add_parser("admin")
    admin_parser.add_argument("--enable", action="store_true")

    image_parser = subparsers.add_parser("image")
    image_parser.add_argument("--install", action="store_true")
    image_parser.add_argument("--enable", action="store_true")
    image_parser.add_argument("--optimize", action="store_true")
    image_parser.add_argument("--delete-originals", action="store_true")

    assets_parser = subparsers.add_parser("assets")
    assets_parser.add_argument("--install", action="store_true")
    assets_parser.add_argument("--minify", action="store_true")

    subparsers.add_parser("deploy")
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument(
        "--keep-source",
        action="store_true",
        help="Keep .py source files along with bytecode",
    )
    build_parser.add_argument(
        "--output", "-o", default=None, help="Output directory name"
    )
    build_parser.add_argument(
        "--with-db",
        action="store_true",
        help="Include current SQLite database in the build",
    )

    dev_parser = subparsers.add_parser("dev")
    dev_parser.add_argument("--port", type=int, default=8000)

    preview_parser = subparsers.add_parser("preview")
    preview_parser.add_argument("--port", type=int, default=8000)

    migrate_parser = subparsers.add_parser("migrate")
    migrate_parser.add_argument("--rollback", action="store_true")
    migrate_parser.add_argument("--status", action="store_true")
    migrate_parser.add_argument("--fake", action="store_true")

    subparsers.add_parser("seed")
    subparsers.add_parser("routes")
    subparsers.add_parser("shell")
    subparsers.add_parser("test").add_argument("path", nargs="?", default=None)

    make_parser = subparsers.add_parser("make")
    make_parser.add_argument(
        "type", choices=["model", "middleware", "page", "component", "migration"]
    )
    make_parser.add_argument("name", nargs="?", default=None)

    # Catch empty args, help or version request
    if len(sys.argv) == 1 or "-h" in sys.argv or "--help" in sys.argv:
        print_help()
        return

    if "-v" in sys.argv or "--version" in sys.argv:
        print(f"Asok Framework v{__version__}")
        return

    args = parser.parse_args()

    if args.command == "create":
        scaffold(args.name, tailwind=args.tailwind, admin=args.admin, image=args.image)
    elif args.command == "createsuperuser":
        run_createsuperuser(args.email, args.password)
    elif args.command == "tailwind":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return

        try:
            if args.enable:
                tailwind_enable(root)
            elif args.install:
                tailwind_install(root, verbose=True)
            elif args.build:
                from .server import _project_uses_tailwind

                if not _project_uses_tailwind(root):
                    Style.warn("This project doesn't use Tailwind.")
                    print("  To enable it, run: asok tailwind --enable")
                    return
                tailwind_build(root, minify=args.minify)
            else:
                tw_parser.print_help()
        except RuntimeError as e:
            Style.error(str(e))
            sys.exit(1)
    elif args.command == "admin":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.enable:
            admin_enable(root)
        else:
            admin_parser.print_help()
    elif args.command == "image":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.enable:
            image_enable(root)
        elif args.install:
            image_install(root)
        elif args.optimize:
            image_optimize_all(root, delete_originals=args.delete_originals)
        else:
            image_parser.print_help()
    elif args.command == "assets":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        if args.install:
            assets_install(root)
        elif args.minify:
            assets_minify(root)
        else:
            assets_parser.print_help()
    elif args.command == "deploy":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        run_deploy(root)
    elif args.command == "build":
        root = _find_project_root()
        if not root:
            Style.error("Not inside an Asok project (no wsgi.py/c found).")
            return
        run_build(
            root,
            keep_source=args.keep_source,
            with_db=args.with_db,
            output=args.output,
        )
    elif args.command == "dev":
        run_dev(args.port)
    elif args.command == "preview":
        run_preview(args.port)
    elif args.command == "migrate":
        run_migrate(rollback=args.rollback, status=args.status, fake=args.fake)
    elif args.command == "seed":
        run_seed()
    elif args.command == "routes":
        run_routes()
    elif args.command == "shell":
        run_shell()
    elif args.command == "test":
        run_test(args.path)
    elif args.command == "make":
        if args.type == "migration":
            make_migration(args.name or "auto_migration")
        elif not args.name:
            Style.error(f"Please provide a name for the {args.type}.")
            sys.exit(1)
        elif args.type == "model":
            make_model(args.name)
        elif args.type == "middleware":
            make_middleware(args.name)
        elif args.type == "page":
            make_page(args.name)
        elif args.type == "component":
            make_component(args.name)
    else:
        print_help()


if __name__ == "__main__":
    main()
